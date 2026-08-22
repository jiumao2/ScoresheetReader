from __future__ import annotations

import asyncio
import hashlib
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from .alignment import align_image
from .database import (
    DocumentNotFoundError,
    DocumentRepository,
    GameNotFoundError,
    RecognitionRunNotFoundError,
    RevisionConflictError,
)
from .master_data import MasterDataValidationError, load_master_data
from .models import (
    AlignmentRequest,
    ConfirmRequest,
    DocumentChangeLogPage,
    DocumentRecognitionResponse,
    DocumentStatus,
    DocumentUpdate,
    GamePriorSnapshot,
    GameSummary,
    Header,
    OfficialEntry,
    RecognitionApplyRequest,
    RecognitionDiff,
    RecognitionRequest,
    RecognitionRun,
    ScoresheetDocument,
    SourceAsset,
    TeamEntry,
    TeamSide,
    ValidationRequest,
)
from .recognition import (
    RecognitionProvider,
    RecognitionProviderError,
    RecognitionQueue,
    RecognitionService,
)
from .renderer import render_pdf, render_svg
from .settings import Settings
from .settings import settings as default_settings
from .template import load_template_definition
from .validation import validate_document

UPLOAD_CHUNK_BYTES = 1024 * 1024
SUPPORTED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def _immutable_update(field: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "IMMUTABLE_DOCUMENT_FIELD",
            "message": f"字段 {field} 只能由专用服务端接口修改。",
            "field": field,
        },
    )


def _prepare_document_update(
    current: ScoresheetDocument,
    incoming: ScoresheetDocument,
    base_revision: int,
) -> ScoresheetDocument:
    """Accept semantic edits while preserving server-owned document state."""
    if incoming.id != current.id:
        raise _immutable_update("id")
    if incoming.revision != base_revision:
        raise _immutable_update("revision")
    if incoming.status not in {DocumentStatus.DRAFT, DocumentStatus.NEEDS_REVIEW}:
        raise _immutable_update("status")
    for field in ("source", "game_prior", "template_id", "rules_profile"):
        if getattr(incoming, field) != getattr(current, field):
            raise _immutable_update(field)

    prepared = incoming.model_copy(deep=True)
    prepared.source = current.source.model_copy(deep=True)
    prepared.game_prior = (
        current.game_prior.model_copy(deep=True) if current.game_prior is not None else None
    )

    if current.recognition is None:
        if incoming.recognition is not None:
            raise _immutable_update("recognition")
        prepared.recognition = None
    else:
        if incoming.recognition is None:
            raise _immutable_update("recognition")
        for field in ("run_id", "notes", "applied_at"):
            if getattr(incoming.recognition, field) != getattr(current.recognition, field):
                raise _immutable_update(f"recognition.{field}")
        if not set(incoming.recognition.problem_paths).issubset(current.recognition.problem_paths):
            raise _immutable_update("recognition.problem_paths")
        current_issues = {issue.model_dump_json() for issue in current.recognition.issues}
        if any(
            issue.model_dump_json() not in current_issues for issue in incoming.recognition.issues
        ):
            raise _immutable_update("recognition.issues")
        prepared.recognition = current.recognition.model_copy(deep=True)
        prepared.recognition.table_personnel = list(incoming.recognition.table_personnel)
        prepared.recognition.problem_paths = list(incoming.recognition.problem_paths)
        prepared.recognition.issues = [
            issue.model_copy(deep=True) for issue in incoming.recognition.issues
        ]

    if current.game_prior is not None:
        prepared.header.competition = current.header.competition
        prepared.header.date = current.header.date
        prepared.header.scheduled_time = current.header.scheduled_time
        prepared.header.venue = current.header.venue
        current_teams = {team.side: team for team in current.teams}
        for team in prepared.teams:
            team.name = current_teams[team.side].name

    prepared.acknowledged_warnings = []
    prepared.status = (
        DocumentStatus.NEEDS_REVIEW if prepared.recognition is not None else DocumentStatus.DRAFT
    )
    return prepared


def _blank_document(
    document_id: str,
    source: SourceAsset,
    prior: GamePriorSnapshot | None = None,
) -> ScoresheetDocument:
    return ScoresheetDocument(
        id=document_id,
        source=source,
        game_prior=prior,
        header=Header(
            competition=prior.competition if prior else "",
            date=prior.date if prior else "",
            scheduled_time=prior.scheduled_time if prior else "",
            venue=prior.venue if prior else "",
        ),
        teams=[
            TeamEntry(side=TeamSide.A, name=prior.team_a.name if prior else ""),
            TeamEntry(side=TeamSide.B, name=prior.team_b.name if prior else ""),
        ],
        officials=[
            OfficialEntry(role="scorer"),
            OfficialEntry(role="assistant_scorer"),
            OfficialEntry(role="timer"),
            OfficialEntry(role="shot_clock_operator"),
            OfficialEntry(role="crew_chief"),
            OfficialEntry(role="umpire_1"),
            OfficialEntry(role="umpire_2"),
            OfficialEntry(role="protest_captain"),
        ],
    )


async def _store_upload(
    file: UploadFile,
    app_settings: Settings,
    *,
    document_id: str | None = None,
    version: int = 0,
) -> tuple[str, SourceAsset, Path]:
    app_settings.upload_dir.mkdir(parents=True, exist_ok=True)
    document_id = document_id or str(uuid4())
    incoming_path = app_settings.upload_dir / f".upload-{uuid4().hex}.tmp"
    normalized_path = app_settings.upload_dir / f".normalized-{uuid4().hex}.tmp"
    created_paths: list[Path] = []
    normalized: Image.Image | None = None
    try:
        with incoming_path.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                output.write(chunk)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(incoming_path) as image:
                image.verify()
            with Image.open(incoming_path) as image:
                image_format = image.format or ""
                orientation = image.getexif().get(274, 1)
                if image_format not in SUPPORTED_FORMATS:
                    raise HTTPException(status_code=415, detail="只支持 JPEG、PNG 或 WebP 图片。")
                if orientation in (0, 1):
                    width, height = image.size
                else:
                    normalized = ImageOps.exif_transpose(image)
                    width, height = normalized.size
                    normalized.load()

            suffix = SUPPORTED_FORMATS[image_format]
            source_path = app_settings.upload_dir / f"{document_id}-source-v{version}{suffix}"
            if orientation in (0, 1):
                incoming_path.replace(source_path)
                created_paths.append(source_path)
            else:
                raw_path = app_settings.upload_dir / f"{document_id}-raw-v{version}{suffix}"
                incoming_path.replace(raw_path)
                created_paths.append(raw_path)
                if image_format == "JPEG":
                    normalized.convert("RGB").save(
                        normalized_path,
                        format="JPEG",
                        quality=95,
                        subsampling=0,
                        optimize=True,
                    )
                elif image_format == "PNG":
                    normalized.save(normalized_path, format="PNG", optimize=True)
                else:
                    normalized.save(normalized_path, format="WEBP", quality=95)
                normalized_path.replace(source_path)
                created_paths.append(source_path)

        digest = hashlib.sha256()
        with source_path.open("rb") as source_stream:
            while chunk := source_stream.read(UPLOAD_CHUNK_BYTES):
                digest.update(chunk)
        source = SourceAsset(
            original_filename=file.filename or f"upload{suffix}",
            original_url=f"/api/v1/documents/{document_id}/source?version={version}",
            version=version,
            content_sha256=digest.hexdigest(),
            width=width,
            height=height,
        )
        return document_id, source, source_path
    except HTTPException:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail="图片解码尺寸触发 Pillow 安全保护，请检查是否为异常超大图片。",
        ) from error
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail="只支持 JPEG、PNG 或 WebP 图片。") from error
    finally:
        if normalized is not None:
            normalized.close()
        incoming_path.unlink(missing_ok=True)
        normalized_path.unlink(missing_ok=True)


def create_app(
    app_settings: Settings = default_settings,
    repository: DocumentRepository | None = None,
    recognition_provider: RecognitionProvider | None = None,
) -> FastAPI:
    app_settings.ensure_directories()
    repo = repository or DocumentRepository(app_settings.database_path)
    master_data_error = ""
    try:
        bundle = load_master_data(app_settings)
        if bundle is not None:
            repo.sync_master_data(bundle)
    except MasterDataValidationError as error:
        master_data_error = str(error)
    recognition_service = RecognitionService(repo, app_settings, recognition_provider)
    recognition_queue = RecognitionQueue(
        repo,
        recognition_service,
        app_settings.recognition_concurrency,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recognition_queue.start()
        try:
            yield
        finally:
            recognition_queue.stop()

    app = FastAPI(
        title="ScoresheetReader API",
        version="0.2.0",
        description="Local semantic scoresheet editor with upload-triggered Qwen recognition.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.repository = repo
    app.state.recognition_service = recognition_service
    app.state.recognition_queue = recognition_queue
    app.state.master_data_error = master_data_error
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_document(document_id: str) -> ScoresheetDocument:
        try:
            return repo.get(document_id)
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="记录表不存在。") from error

    def update_document(
        document_id: str,
        base_revision: int,
        document: ScoresheetDocument,
        source: str,
    ) -> ScoresheetDocument:
        try:
            return repo.update(document_id, base_revision, document, source)
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="记录表不存在。") from error
        except RevisionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "草稿已被更新，请重新载入后再保存。",
                    "expected": error.expected,
                    "actual": error.actual,
                },
            ) from error

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        master_status = (
            "error" if master_data_error else ("ready" if repo.list_games() else "empty")
        )
        return {
            "status": "ok",
            "recognition": "mock" if app_settings.recognition_mode == "mock" else "automatic",
            "master_data": master_status,
        }

    @app.get("/api/v1/template/definition")
    def template_definition() -> dict:
        return load_template_definition()

    @app.get("/api/v1/template/pdf")
    def template_pdf() -> FileResponse:
        if not app_settings.template_path.exists():
            raise HTTPException(
                status_code=404,
                detail="未找到本地模板 PDF，请设置 SCORESHEET_TEMPLATE_PATH。",
            )
        return FileResponse(app_settings.template_path, media_type="application/pdf")

    @app.get("/api/v1/games", response_model=list[GameSummary])
    def list_games() -> list[GameSummary]:
        if master_data_error:
            raise HTTPException(status_code=503, detail=master_data_error)
        return repo.list_games()

    @app.get("/api/v1/games/{game_id}")
    def read_game(game_id: str):
        try:
            return repo.get_game(game_id)
        except GameNotFoundError as error:
            raise HTTPException(status_code=404, detail="比赛不存在。") from error

    @app.post(
        "/api/v1/games/{game_id}/documents",
        response_model=DocumentRecognitionResponse,
        status_code=201,
    )
    async def create_game_document(
        game_id: str,
        file: Annotated[UploadFile, File()],
    ) -> DocumentRecognitionResponse:
        try:
            game = repo.get_game(game_id)
        except GameNotFoundError as error:
            raise HTTPException(status_code=404, detail="比赛不存在。") from error
        if not game.ready or game.prior is None:
            raise HTTPException(
                status_code=409,
                detail=game.unavailable_reason or "该比赛的参赛球队尚未确定。",
            )
        document_id, source, _ = await _store_upload(file, app_settings)
        document = repo.create(
            _blank_document(document_id, source, game.prior),
            source="game_upload",
        )
        run, _ = recognition_service.create_run(
            document.id,
            document.revision,
            trigger="upload",
            force_new=True,
            use_cache=False,
        )
        recognition_queue.wake()
        return DocumentRecognitionResponse(document=document, recognition_run=run)

    @app.put(
        "/api/v1/documents/{document_id}/source",
        response_model=DocumentRecognitionResponse,
    )
    async def replace_document_source(
        document_id: str,
        file: Annotated[UploadFile, File()],
        base_revision: Annotated[int, Form(ge=0)],
    ) -> DocumentRecognitionResponse:
        current = get_document(document_id)
        if current.game_prior is None:
            raise HTTPException(status_code=409, detail="该草稿没有比赛先验，不能自动识别。")
        if current.revision != base_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "重新上传前草稿已发生变化，请重新载入。",
                    "expected": base_revision,
                    "actual": current.revision,
                },
            )
        next_version = current.source.version + 1
        _, source, source_path = await _store_upload(
            file,
            app_settings,
            document_id=document_id,
            version=next_version,
        )
        replacement = _blank_document(document_id, source, current.game_prior)
        try:
            document = repo.update(document_id, base_revision, replacement, "reupload")
        except RevisionConflictError as error:
            source_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "重新上传前草稿已发生变化，请重新载入。",
                    "expected": error.expected,
                    "actual": error.actual,
                },
            ) from error
        run, _ = recognition_service.create_run(
            document.id,
            document.revision,
            trigger="reupload",
            force_new=True,
            use_cache=False,
        )
        recognition_queue.wake()
        return DocumentRecognitionResponse(document=document, recognition_run=run)

    @app.get("/api/v1/documents/{document_id}", response_model=ScoresheetDocument)
    def read_document(document_id: str) -> ScoresheetDocument:
        return get_document(document_id)

    @app.get(
        "/api/v1/documents/{document_id}/changes",
        response_model=DocumentChangeLogPage,
    )
    def read_changes(
        document_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        before_id: int | None = Query(default=None, ge=1),
    ) -> DocumentChangeLogPage:
        try:
            return repo.changes(document_id, limit=limit, before_id=before_id)
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="记录表不存在。") from error

    @app.post(
        "/api/v1/documents/{document_id}/recognitions",
        response_model=RecognitionRun,
        status_code=202,
    )
    def create_recognition(
        document_id: str,
        request: RecognitionRequest,
    ) -> RecognitionRun:
        try:
            document = repo.get(document_id)
            if document.revision != request.base_revision:
                raise RevisionConflictError(request.base_revision, document.revision)
            latest = repo.latest_recognition_run(document_id)
            if latest is None:
                raise RecognitionProviderError("上传比赛记录表后会自动识别，无需手动启动。")
            if latest.status in {"pending", "connecting", "thinking", "structuring", "validating"}:
                return latest
            if latest.status not in {"failed", "interrupted"}:
                raise RecognitionProviderError("当前图片已有成功识别结果，不支持重复识别。")
            run, needs_execution = recognition_service.create_run(
                document_id,
                request.base_revision,
                trigger="retry",
                force_new=True,
                use_cache=False,
            )
        except DocumentNotFoundError as error:
            raise HTTPException(status_code=404, detail="记录表不存在。") from error
        except RevisionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "识别前草稿已发生变化，请保存并重试。",
                    "expected": error.expected,
                    "actual": error.actual,
                },
            ) from error
        except RecognitionProviderError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if needs_execution:
            recognition_queue.wake()
        return run

    @app.get(
        "/api/v1/documents/{document_id}/recognitions/latest",
        response_model=RecognitionRun | None,
    )
    def latest_recognition(document_id: str) -> RecognitionRun | None:
        get_document(document_id)
        return repo.latest_recognition_run(document_id)

    @app.get("/api/v1/recognitions/{run_id}", response_model=RecognitionRun)
    def read_recognition(run_id: str) -> RecognitionRun:
        try:
            return repo.get_recognition_run(run_id)
        except RecognitionRunNotFoundError as error:
            raise HTTPException(status_code=404, detail="识别任务不存在。") from error

    @app.get("/api/v1/recognitions/{run_id}/events")
    async def recognition_events(run_id: str) -> StreamingResponse:
        try:
            repo.get_recognition_run(run_id)
        except RecognitionRunNotFoundError as error:
            raise HTTPException(status_code=404, detail="识别任务不存在。") from error

        async def stream():
            last_update = ""
            while True:
                try:
                    run = repo.get_recognition_run(run_id)
                except RecognitionRunNotFoundError:
                    break
                marker = f"{run.status}:{run.updated_at.isoformat()}"
                if marker != last_update:
                    yield f"data: {run.model_dump_json()}\n\n"
                    last_update = marker
                if run.status in {"succeeded", "failed", "superseded", "interrupted"}:
                    break
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/recognitions/{run_id}/diff", response_model=RecognitionDiff)
    def recognition_diff(run_id: str) -> RecognitionDiff:
        try:
            return recognition_service.diff(run_id)
        except RecognitionRunNotFoundError as error:
            raise HTTPException(status_code=404, detail="识别任务不存在。") from error
        except RecognitionProviderError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/recognitions/{run_id}/apply",
        response_model=ScoresheetDocument,
    )
    def apply_recognition(
        run_id: str,
        request: RecognitionApplyRequest,
    ) -> ScoresheetDocument:
        try:
            return recognition_service.apply(
                run_id,
                request.base_revision,
                set(request.regions),
            )
        except RecognitionRunNotFoundError as error:
            raise HTTPException(status_code=404, detail="识别任务不存在。") from error
        except RevisionConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "合并前草稿已发生变化，请重新查看差异。",
                    "expected": error.expected,
                    "actual": error.actual,
                },
            ) from error
        except (RecognitionProviderError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.patch("/api/v1/documents/{document_id}", response_model=ScoresheetDocument)
    def patch_document(document_id: str, update: DocumentUpdate) -> ScoresheetDocument:
        current = get_document(document_id)
        prepared = _prepare_document_update(
            current,
            update.document,
            update.base_revision,
        )
        return update_document(
            document_id,
            update.base_revision,
            prepared,
            update.source,
        )

    @app.post("/api/v1/documents/{document_id}/alignment", response_model=ScoresheetDocument)
    def set_alignment(document_id: str, request: AlignmentRequest) -> ScoresheetDocument:
        document = get_document(document_id)
        if request.base_revision != document.revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "校正前草稿已发生变化。",
                    "expected": request.base_revision,
                    "actual": document.revision,
                },
            )
        source_candidates = list(
            app_settings.upload_dir.glob(f"{document_id}-source-v{document.source.version}.*")
        )
        if not source_candidates and document.source.version == 0:
            source_candidates = list(app_settings.upload_dir.glob(f"{document_id}-original.*"))
        if not source_candidates:
            raise HTTPException(status_code=404, detail="原始图片不存在。")
        destination = (
            app_settings.upload_dir / f"{document_id}-aligned-v{document.source.version}.jpg"
        )
        try:
            width, height = align_image(
                source_candidates[0],
                destination,
                request.rotation,
                request.corners,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        document.source.rotation = request.rotation
        document.source.corners = request.corners
        document.source.aligned_url = (
            f"/api/v1/documents/{document_id}/source?aligned=true&version={document.source.version}"
        )
        document.source.width = width
        document.source.height = height
        return update_document(document_id, request.base_revision, document, "alignment")

    @app.get("/api/v1/documents/{document_id}/source")
    def source_image(
        document_id: str,
        aligned: bool = Query(default=False),
        version: int | None = Query(default=None, ge=0),
    ) -> FileResponse:
        document = get_document(document_id)
        requested_version = document.source.version if version is None else version
        if requested_version != document.source.version:
            raise HTTPException(status_code=404, detail="该图片版本已不是当前记录表原图。")
        pattern = (
            f"{document_id}-aligned-v{requested_version}.jpg"
            if aligned
            else f"{document_id}-source-v{requested_version}.*"
        )
        candidates = list(app_settings.upload_dir.glob(pattern))
        if not candidates and requested_version == 0:
            legacy_pattern = (
                f"{document_id}-aligned.jpg" if aligned else f"{document_id}-original.*"
            )
            candidates = list(app_settings.upload_dir.glob(legacy_pattern))
        if not candidates:
            raise HTTPException(status_code=404, detail="图片不存在。")
        return FileResponse(candidates[0])

    @app.post("/api/v1/documents/{document_id}/validate")
    def validate(document_id: str, request: ValidationRequest):
        document = get_document(document_id)
        if request.base_revision != document.revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "校验前草稿已发生变化，请重新载入后再试。",
                    "expected": request.base_revision,
                    "actual": document.revision,
                },
            )
        return validate_document(document, app_settings.rule_profiles_path)

    @app.post("/api/v1/documents/{document_id}/confirm", response_model=ScoresheetDocument)
    def confirm(document_id: str, request: ConfirmRequest) -> ScoresheetDocument:
        document = get_document(document_id)
        if request.base_revision != document.revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REVISION_CONFLICT",
                    "message": "提交前草稿已发生变化，请重新校验后再试。",
                    "expected": request.base_revision,
                    "actual": document.revision,
                },
            )
        report = validate_document(document, app_settings.rule_profiles_path)
        errors = [issue for issue in report.issues if issue.severity == "error"]
        warnings = [issue for issue in report.issues if issue.severity == "warning"]
        warning_codes = {issue.code for issue in warnings}
        acknowledged = set(request.acknowledge_warning_codes)
        if errors or warning_codes - acknowledged:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "VALIDATION_FAILED",
                    "report": report.model_dump(mode="json"),
                    "unacknowledged_warnings": sorted(warning_codes - acknowledged),
                },
            )
        document.acknowledged_warnings = sorted(acknowledged & warning_codes)
        document.status = DocumentStatus.CONFIRMED
        return update_document(document_id, request.base_revision, document, "confirm")

    @app.get("/api/v1/documents/{document_id}/render.svg")
    def document_svg(document_id: str) -> Response:
        return Response(render_svg(get_document(document_id)), media_type="image/svg+xml")

    @app.get("/api/v1/documents/{document_id}/render.pdf")
    def document_pdf(document_id: str) -> Response:
        document = get_document(document_id)
        try:
            payload = render_pdf(document, app_settings.template_path)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(
            payload,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="scoresheet-{document_id}.pdf"'},
        )

    return app


app = create_app()
