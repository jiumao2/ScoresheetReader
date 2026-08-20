from __future__ import annotations

import asyncio
import io
from typing import Annotated
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
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
from .fixtures import synthetic_document
from .master_data import MasterDataValidationError, load_master_data
from .models import (
    AlignmentRequest,
    ConfirmRequest,
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
)
from .recognition import RecognitionProvider, RecognitionProviderError, RecognitionService
from .renderer import render_pdf, render_svg
from .settings import Settings
from .settings import settings as default_settings
from .template import load_template_definition
from .validation import validate_document

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


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


async def _store_upload(file: UploadFile, app_settings: Settings) -> tuple[str, SourceAsset]:
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 25 MB。")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            image_format = image.format or ""
            orientation = image.getexif().get(274, 1)
            normalized = ImageOps.exif_transpose(image)
            width, height = normalized.size
            normalized.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise HTTPException(status_code=415, detail="只支持 JPEG、PNG 或 WebP 图片。") from error
    if image_format not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=415, detail="只支持 JPEG、PNG 或 WebP 图片。")

    document_id = str(uuid4())
    suffix = SUPPORTED_FORMATS[image_format]
    source_path = app_settings.upload_dir / f"{document_id}-original{suffix}"
    if orientation in (0, 1):
        source_path.write_bytes(payload)
    else:
        raw_path = app_settings.upload_dir / f"{document_id}-raw{suffix}"
        raw_path.write_bytes(payload)
        if image_format == "JPEG":
            normalized.convert("RGB").save(source_path, format="JPEG", quality=95)
        elif image_format == "PNG":
            normalized.save(source_path, format="PNG", optimize=True)
        else:
            normalized.save(source_path, format="WEBP", quality=95)
    source = SourceAsset(
        original_filename=file.filename or f"upload{suffix}",
        original_url=f"/api/v1/documents/{document_id}/source",
        width=width,
        height=height,
    )
    return document_id, source


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
    app = FastAPI(
        title="ScoresheetReader API",
        version="0.2.0",
        description="Local semantic scoresheet editor with opt-in Qwen whole-image recognition.",
    )
    app.state.settings = app_settings
    app.state.repository = repo
    app.state.recognition_service = recognition_service
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
            "recognition": "mock" if app_settings.recognition_mode == "mock" else "on_demand",
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

    @app.get("/api/v1/fixtures/synthetic", response_model=ScoresheetDocument)
    def fixture() -> ScoresheetDocument:
        return synthetic_document("synthetic-preview")

    @app.post("/api/v1/fixtures/synthetic", response_model=ScoresheetDocument, status_code=201)
    def create_fixture() -> ScoresheetDocument:
        return repo.create(synthetic_document(), source="synthetic")

    @app.post("/api/v1/documents", response_model=ScoresheetDocument, status_code=201)
    async def create_document(file: Annotated[UploadFile, File()]) -> ScoresheetDocument:
        document_id, source = await _store_upload(file, app_settings)
        return repo.create(_blank_document(document_id, source), source="upload")

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
        response_model=ScoresheetDocument,
        status_code=201,
    )
    async def create_game_document(
        game_id: str,
        file: Annotated[UploadFile, File()],
    ) -> ScoresheetDocument:
        try:
            game = repo.get_game(game_id)
        except GameNotFoundError as error:
            raise HTTPException(status_code=404, detail="比赛不存在。") from error
        if not game.ready or game.prior is None:
            raise HTTPException(
                status_code=409,
                detail=game.unavailable_reason or "该比赛的参赛球队尚未确定。",
            )
        document_id, source = await _store_upload(file, app_settings)
        return repo.create(
            _blank_document(document_id, source, game.prior),
            source="game_upload",
        )

    @app.get("/api/v1/documents/{document_id}", response_model=ScoresheetDocument)
    def read_document(document_id: str) -> ScoresheetDocument:
        return get_document(document_id)

    @app.get("/api/v1/documents/{document_id}/revisions")
    def read_revisions(document_id: str):
        try:
            return repo.revisions(document_id)
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
        background_tasks: BackgroundTasks,
    ) -> RecognitionRun:
        try:
            run, needs_execution = recognition_service.create_run(
                document_id,
                request.base_revision,
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
            background_tasks.add_task(recognition_service.execute, run.id)
        return run

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
                if run.status in {"succeeded", "failed"}:
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
        return update_document(
            document_id,
            update.base_revision,
            update.document,
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
        source_candidates = list(app_settings.upload_dir.glob(f"{document_id}-original.*"))
        if not source_candidates:
            raise HTTPException(status_code=404, detail="原始图片不存在。")
        destination = app_settings.upload_dir / f"{document_id}-aligned.jpg"
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
        document.source.aligned_url = f"/api/v1/documents/{document_id}/source?aligned=true"
        document.source.width = width
        document.source.height = height
        return update_document(document_id, request.base_revision, document, "alignment")

    @app.get("/api/v1/documents/{document_id}/source")
    def source_image(document_id: str, aligned: bool = Query(default=False)) -> FileResponse:
        get_document(document_id)
        pattern = f"{document_id}-aligned.jpg" if aligned else f"{document_id}-original.*"
        candidates = list(app_settings.upload_dir.glob(pattern))
        if not candidates:
            raise HTTPException(status_code=404, detail="图片不存在。")
        return FileResponse(candidates[0])

    @app.post("/api/v1/documents/{document_id}/validate")
    def validate(document_id: str):
        return validate_document(get_document(document_id), app_settings.rule_profiles_path)

    @app.post("/api/v1/documents/{document_id}/confirm", response_model=ScoresheetDocument)
    def confirm(document_id: str, request: ConfirmRequest) -> ScoresheetDocument:
        document = get_document(document_id)
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
        document.acknowledged_warnings = sorted(acknowledged)
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
