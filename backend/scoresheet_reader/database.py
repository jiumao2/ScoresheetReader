from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from .change_log import log_payload_for_update
from .master_data import MasterDataBundle
from .models import (
    DocumentChangeLogEntry,
    DocumentChangeLogPage,
    DocumentStatus,
    FieldChange,
    GameDetail,
    GamePriorSnapshot,
    GameSummary,
    PriorTeam,
    RecognitionRun,
    RecognitionUsage,
    ScoresheetDocument,
)


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChangeLogRow(Base):
    __tablename__ = "document_change_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(160), nullable=False)
    changes_payload: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MasterStateRow(Base):
    __tablename__ = "master_data_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class TeamRow(Base):
    __tablename__ = "master_teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    division: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class PlayerRow(Base):
    __tablename__ = "master_players"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class GameRow(Base):
    __tablename__ = "master_games"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    competition: Mapped[str] = mapped_column(String(128), nullable=False)
    division: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    game_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    scheduled_time: Mapped[str] = mapped_column(String(5), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    venue: Mapped[str] = mapped_column(String(128), nullable=False)
    team_a_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_b_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_a_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team_b_name: Mapped[str] = mapped_column(String(128), nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unavailable_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RecognitionRunRow(Base):
    __tablename__ = "recognition_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy")
    source_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    superseded_by_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recognition_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


ACTIVE_RECOGNITION_STATUSES = (
    "pending",
    "connecting",
    "thinking",
    "structuring",
    "validating",
)

EXECUTING_RECOGNITION_STATUSES = (
    "connecting",
    "thinking",
    "structuring",
    "validating",
)


class RevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"Revision conflict: expected {expected}, current revision is {actual}")
        self.expected = expected
        self.actual = actual


class DocumentNotFoundError(KeyError):
    pass


class GameNotFoundError(KeyError):
    pass


class RecognitionRunNotFoundError(KeyError):
    pass


class DocumentRepository:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        Base.metadata.create_all(self.engine)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply SQLite migrations needed by existing local installations."""
        additions = {
            "trigger": "TEXT NOT NULL DEFAULT 'legacy'",
            "source_version": "INTEGER NOT NULL DEFAULT 0",
            "image_sha256": "TEXT NOT NULL DEFAULT ''",
            "superseded_by_run_id": "TEXT",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }
        with self.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.execute(text("PRAGMA table_info(recognition_runs)"))
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE recognition_runs ADD COLUMN {name} {definition}"
                    )
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS uq_active_recognition_document_cache_key"
            )
        self._migrate_revision_snapshots()

    @staticmethod
    def _migration_datetime(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(value)

    def _migrate_revision_snapshots(self) -> None:
        """Convert legacy full-document snapshots to compact, non-restorable logs."""
        with self.engine.begin() as connection:
            legacy_exists = connection.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'document_revisions'"
                )
            ).first()
            if legacy_exists is None:
                connection.execute(
                    text(
                        "INSERT INTO master_data_state(key, value) "
                        "VALUES ('change_log_schema', '1') "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                    )
                )
                return

            rows = connection.execute(
                text(
                    "SELECT document_id, revision, source, payload, created_at "
                    "FROM document_revisions ORDER BY document_id, revision"
                )
            ).mappings()
            previous_by_document: dict[str, ScoresheetDocument] = {}
            for row in rows:
                document_id = str(row["document_id"])
                current = ScoresheetDocument.model_validate_json(row["payload"])
                previous = previous_by_document.get(document_id)
                previous_by_document[document_id] = current
                if previous is None:
                    continue
                entry = log_payload_for_update(previous, current, str(row["source"]))
                if entry is None:
                    continue
                action, summary, changes = entry
                connection.execute(
                    ChangeLogRow.__table__.insert().values(
                        document_id=document_id,
                        action=action,
                        summary=summary,
                        changes_payload=json.dumps(
                            [change.model_dump(mode="json") for change in changes],
                            ensure_ascii=False,
                        ),
                        created_at=self._migration_datetime(row["created_at"]),
                    )
                )

            connection.exec_driver_sql("DROP TABLE document_revisions")
            connection.execute(
                text(
                    "INSERT INTO master_data_state(key, value) "
                    "VALUES ('change_log_schema', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                )
            )

    def sync_master_data(self, bundle: MasterDataBundle) -> bool:
        """Replace only derived master tables when their private source hash changes."""
        with Session(self.engine) as session:
            state = session.get(MasterStateRow, "source_hash")
            if state is not None and state.value == bundle.source_hash:
                return False
            session.execute(delete(PlayerRow))
            session.execute(delete(TeamRow))
            session.execute(delete(GameRow))
            for team in bundle.teams:
                session.add(TeamRow(id=team.id, division=team.division, name=team.name))
                for order, (player_id, player_name) in enumerate(team.players, start=1):
                    session.add(
                        PlayerRow(
                            id=player_id,
                            team_id=team.id,
                            name=player_name,
                            sort_order=order,
                        )
                    )
            for game in bundle.games:
                session.add(
                    GameRow(
                        id=game.id,
                        competition=game.competition,
                        division=game.division,
                        game_date=game.date,
                        scheduled_time=game.scheduled_time,
                        scheduled_at=game.scheduled_at,
                        venue=game.venue,
                        team_a_id=game.team_a_id,
                        team_b_id=game.team_b_id,
                        team_a_name=game.team_a_name,
                        team_b_name=game.team_b_name,
                        ready=game.ready,
                        unavailable_reason=game.unavailable_reason,
                    )
                )
            if state is None:
                session.add(MasterStateRow(key="source_hash", value=bundle.source_hash))
            else:
                state.value = bundle.source_hash
            session.commit()
            return True

    def _latest_game_documents(self, session: Session) -> dict[str, ScoresheetDocument]:
        latest: dict[str, ScoresheetDocument] = {}
        rows = session.scalars(
            select(DocumentRow).order_by(
                DocumentRow.updated_at.desc(),
                DocumentRow.created_at.desc(),
                DocumentRow.id.desc(),
            )
        ).all()
        for row in rows:
            document = ScoresheetDocument.model_validate_json(row.payload)
            prior = document.game_prior
            if prior is None or prior.game_id in latest:
                continue
            latest[prior.game_id] = document
        return latest

    def _game_summary(
        self,
        row: GameRow,
        document: ScoresheetDocument | None = None,
        recognition_run: RecognitionRun | None = None,
    ) -> GameSummary:
        scoresheet_state = "not_uploaded"
        if document is not None:
            if document.status == DocumentStatus.CONFIRMED:
                scoresheet_state = "confirmed"
            elif (
                recognition_run is not None
                and recognition_run.status in ACTIVE_RECOGNITION_STATUSES
            ):
                scoresheet_state = "recognizing"
            elif document.recognition is not None or (
                recognition_run is not None and recognition_run.status == "succeeded"
            ):
                scoresheet_state = "recognized"
            else:
                scoresheet_state = "recognition_failed"
        return GameSummary(
            id=row.id,
            competition=row.competition,
            division=row.division,
            date=row.game_date,
            scheduled_time=row.scheduled_time,
            venue=row.venue,
            team_a_name=row.team_a_name,
            team_b_name=row.team_b_name,
            ready=row.ready,
            unavailable_reason=row.unavailable_reason,
            document_id=document.id if document is not None else None,
            scoresheet_state=scoresheet_state,
        )

    def list_games(self) -> list[GameSummary]:
        with Session(self.engine) as session:
            rows = session.scalars(select(GameRow).order_by(GameRow.scheduled_at, GameRow.id)).all()
            latest = self._latest_game_documents(session)
            return [
                self._game_summary(
                    row,
                    latest.get(row.id),
                    self._latest_recognition_run(session, latest[row.id].id)
                    if row.id in latest
                    else None,
                )
                for row in rows
            ]

    def get_game(self, game_id: str) -> GameDetail:
        with Session(self.engine) as session:
            row = session.get(GameRow, game_id)
            if row is None:
                raise GameNotFoundError(game_id)
            document = self._latest_game_documents(session).get(row.id)
            summary = self._game_summary(
                row,
                document,
                self._latest_recognition_run(session, document.id) if document else None,
            )
            prior = None
            if row.ready and row.team_a_id and row.team_b_id:
                teams = {
                    team_id: session.get(TeamRow, team_id)
                    for team_id in (row.team_a_id, row.team_b_id)
                }
                names: dict[str, list[str]] = {}
                for team_id in (row.team_a_id, row.team_b_id):
                    names[team_id] = list(
                        session.scalars(
                            select(PlayerRow.name)
                            .where(PlayerRow.team_id == team_id)
                            .order_by(PlayerRow.sort_order)
                        ).all()
                    )
                state = session.get(MasterStateRow, "source_hash")
                prior = GamePriorSnapshot(
                    game_id=row.id,
                    competition=row.competition,
                    division=row.division,
                    date=row.game_date,
                    scheduled_time=row.scheduled_time,
                    venue=row.venue,
                    team_a=PriorTeam(
                        team_id=row.team_a_id,
                        name=teams[row.team_a_id].name,
                        player_names=names[row.team_a_id],
                    ),
                    team_b=PriorTeam(
                        team_id=row.team_b_id,
                        name=teams[row.team_b_id].name,
                        player_names=names[row.team_b_id],
                    ),
                    source_hash=state.value if state else "",
                    locked_paths=[
                        "/header/competition",
                        "/header/date",
                        "/header/scheduled_time",
                        "/header/venue",
                        "/teams/0/name",
                        "/teams/1/name",
                    ],
                )
            return GameDetail(**summary.model_dump(), prior=prior)

    def create(self, document: ScoresheetDocument, source: str = "system") -> ScoresheetDocument:
        now = datetime.now(UTC)
        document.revision = 0
        document.created_at = now
        document.updated_at = now
        payload = document.model_dump_json()
        with Session(self.engine) as session:
            session.add(
                DocumentRow(
                    id=document.id,
                    revision=0,
                    status=document.status.value,
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return document

    def get(self, document_id: str) -> ScoresheetDocument:
        with Session(self.engine) as session:
            row = session.get(DocumentRow, document_id)
            if row is None:
                raise DocumentNotFoundError(document_id)
            return ScoresheetDocument.model_validate_json(row.payload)

    def update(
        self,
        document_id: str,
        base_revision: int,
        document: ScoresheetDocument,
        source: str,
    ) -> ScoresheetDocument:
        with Session(self.engine) as session:
            row = session.get(DocumentRow, document_id)
            if row is None:
                raise DocumentNotFoundError(document_id)
            previous = ScoresheetDocument.model_validate_json(row.payload)

        now = datetime.now(UTC)
        document.id = document_id
        document.revision = base_revision + 1
        document.created_at = previous.created_at
        document.updated_at = now
        payload = document.model_dump_json()
        change_log = log_payload_for_update(previous, document, source)

        with Session(self.engine) as session:
            result = session.execute(
                update(DocumentRow)
                .where(
                    DocumentRow.id == document_id,
                    DocumentRow.revision == base_revision,
                )
                .values(
                    revision=document.revision,
                    status=document.status.value,
                    payload=payload,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                actual = session.scalar(
                    select(DocumentRow.revision).where(DocumentRow.id == document_id)
                )
                if actual is None:
                    raise DocumentNotFoundError(document_id)
                raise RevisionConflictError(base_revision, actual)
            if change_log is not None:
                action, summary, changes = change_log
                session.add(
                    ChangeLogRow(
                        document_id=document_id,
                        action=action,
                        summary=summary,
                        changes_payload=json.dumps(
                            [change.model_dump(mode="json") for change in changes],
                            ensure_ascii=False,
                        ),
                        created_at=now,
                    )
                )
            session.commit()
        return document

    def changes(
        self,
        document_id: str,
        *,
        limit: int = 50,
        before_id: int | None = None,
    ) -> DocumentChangeLogPage:
        with Session(self.engine) as session:
            exists = session.get(DocumentRow, document_id)
            if exists is None:
                raise DocumentNotFoundError(document_id)
            query = select(ChangeLogRow).where(ChangeLogRow.document_id == document_id)
            if before_id is not None:
                query = query.where(ChangeLogRow.id < before_id)
            rows = list(
                session.scalars(query.order_by(ChangeLogRow.id.desc()).limit(limit + 1)).all()
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [
                DocumentChangeLogEntry(
                    id=row.id,
                    document_id=row.document_id,
                    action=row.action,
                    summary=row.summary,
                    changes=[
                        FieldChange.model_validate(change)
                        for change in json.loads(row.changes_payload or "[]")
                    ],
                    created_at=row.created_at,
                )
                for row in rows
            ]
            return DocumentChangeLogPage(
                items=items,
                next_before_id=rows[-1].id if has_more and rows else None,
            )

    @staticmethod
    def _recognition_run(row: RecognitionRunRow) -> RecognitionRun:
        usage = RecognitionUsage.model_validate_json(row.usage_payload or "{}")
        result = json.loads(row.result_payload) if row.result_payload else None
        return RecognitionRun(
            id=row.id,
            document_id=row.document_id,
            base_revision=row.base_revision,
            status=row.status,
            model=row.model,
            prompt_version=row.prompt_version,
            trigger=row.trigger,
            source_version=row.source_version,
            image_sha256=row.image_sha256,
            superseded_by_run_id=row.superseded_by_run_id,
            retry_count=row.retry_count,
            cached=row.cached,
            auto_applied=row.auto_applied,
            applied_revision=row.applied_revision,
            recognition_notes=row.recognition_notes,
            usage=usage,
            error=row.error,
            result=result,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _latest_recognition_run(
        self,
        session: Session,
        document_id: str,
    ) -> RecognitionRun | None:
        row = session.scalars(
            select(RecognitionRunRow)
            .where(RecognitionRunRow.document_id == document_id)
            .order_by(RecognitionRunRow.created_at.desc(), RecognitionRunRow.id.desc())
        ).first()
        return self._recognition_run(row) if row is not None else None

    def latest_recognition_run(self, document_id: str) -> RecognitionRun | None:
        with Session(self.engine) as session:
            return self._latest_recognition_run(session, document_id)

    def find_cached_result(self, cache_key: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(RecognitionRunRow)
                .where(
                    RecognitionRunRow.cache_key == cache_key,
                    RecognitionRunRow.status == "succeeded",
                    RecognitionRunRow.result_payload.is_not(None),
                )
                .order_by(RecognitionRunRow.updated_at.desc())
            ).first()
            return json.loads(row.result_payload) if row and row.result_payload else None

    def find_active_recognition_run(
        self,
        document_id: str,
        cache_key: str,
    ) -> RecognitionRun | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(RecognitionRunRow)
                .where(
                    RecognitionRunRow.document_id == document_id,
                    RecognitionRunRow.cache_key == cache_key,
                    RecognitionRunRow.status.in_(ACTIVE_RECOGNITION_STATUSES),
                )
                .order_by(RecognitionRunRow.created_at)
            ).first()
            return self._recognition_run(row) if row is not None else None

    def create_recognition_run(
        self,
        *,
        run_id: str,
        document_id: str,
        base_revision: int,
        model: str,
        cache_key: str,
        prompt_version: str,
        trigger: str = "legacy",
        source_version: int = 0,
        image_sha256: str = "",
        supersede_existing: bool = False,
        retry_count: int = 0,
        cached_result: dict | None = None,
    ) -> tuple[RecognitionRun, bool]:
        now = datetime.now(UTC)
        row = RecognitionRunRow(
            id=run_id,
            document_id=document_id,
            base_revision=base_revision,
            status="succeeded" if cached_result is not None else "pending",
            model=model,
            cache_key=cache_key,
            prompt_version=prompt_version,
            trigger=trigger,
            source_version=source_version,
            image_sha256=image_sha256,
            retry_count=retry_count,
            cached=cached_result is not None,
            recognition_notes=str((cached_result or {}).get("recognition_notes", "")),
            result_payload=(
                json.dumps(cached_result, ensure_ascii=False) if cached_result is not None else None
            ),
            usage_payload=RecognitionUsage().model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session:
            if supersede_existing:
                older = session.scalars(
                    select(RecognitionRunRow).where(
                        RecognitionRunRow.document_id == document_id,
                        RecognitionRunRow.status.in_(ACTIVE_RECOGNITION_STATUSES),
                    )
                ).all()
                for existing in older:
                    existing.superseded_by_run_id = run_id
                    existing.updated_at = now
                    if existing.status == "pending":
                        existing.status = "superseded"
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row), True

    def get_recognition_run(self, run_id: str) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            return self._recognition_run(row)

    def claim_next_recognition_run(self) -> str | None:
        """Atomically claim the oldest runnable task while serializing each document."""
        with Session(self.engine) as session:
            candidates = session.scalars(
                select(RecognitionRunRow)
                .where(RecognitionRunRow.status == "pending")
                .order_by(RecognitionRunRow.created_at, RecognitionRunRow.id)
            ).all()
            for candidate in candidates:
                executing = session.scalars(
                    select(RecognitionRunRow.id).where(
                        RecognitionRunRow.document_id == candidate.document_id,
                        RecognitionRunRow.status.in_(EXECUTING_RECOGNITION_STATUSES),
                    )
                ).first()
                if executing is not None:
                    continue
                result = session.execute(
                    update(RecognitionRunRow)
                    .where(
                        RecognitionRunRow.id == candidate.id,
                        RecognitionRunRow.status == "pending",
                    )
                    .values(status="connecting", updated_at=datetime.now(UTC))
                )
                if result.rowcount == 1:
                    session.commit()
                    return candidate.id
                session.rollback()
            return None

    def recover_interrupted_recognition_runs(self) -> int:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            result = session.execute(
                update(RecognitionRunRow)
                .where(RecognitionRunRow.status.in_(EXECUTING_RECOGNITION_STATUSES))
                .values(
                    status="interrupted",
                    error="本地服务在识别过程中停止；为避免重复付费，任务未自动重试。",
                    updated_at=now,
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def mark_recognition_status(self, run_id: str, status: str) -> None:
        if status not in {"connecting", "thinking", "structuring", "validating"}:
            raise ValueError(f"Unsupported recognition status: {status}")
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            if row.status in {"superseded", "interrupted", "failed", "succeeded"}:
                return
            row.status = status
            row.updated_at = datetime.now(UTC)
            session.commit()

    def finish_recognition(
        self,
        run_id: str,
        result: dict,
        usage: RecognitionUsage,
        *,
        finalize: bool = True,
    ) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            row.status = (
                "superseded"
                if row.superseded_by_run_id is not None
                else ("succeeded" if finalize else "validating")
            )
            row.result_payload = json.dumps(result, ensure_ascii=False)
            row.recognition_notes = str(result.get("recognition_notes", ""))
            row.usage_payload = usage.model_dump_json()
            row.error = ""
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)

    def mark_recognition_succeeded(self, run_id: str) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            row.status = "superseded" if row.superseded_by_run_id else "succeeded"
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)

    def mark_recognition_applied(
        self,
        run_id: str,
        revision: int,
        *,
        automatic: bool,
    ) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            row.auto_applied = automatic
            row.applied_revision = revision
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)

    def fail_recognition(
        self,
        run_id: str,
        message: str,
        usage: RecognitionUsage | None = None,
    ) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            row.status = "superseded" if row.superseded_by_run_id else "failed"
            row.error = message
            if usage is not None:
                row.usage_payload = usage.model_dump_json()
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)

    def requeue_rate_limited_recognition(self, run_id: str, message: str) -> bool:
        """Requeue one provider-rejected request; never retry a second time."""
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            if row.superseded_by_run_id or row.retry_count >= 1:
                return False
            row.status = "pending"
            row.retry_count += 1
            row.error = message
            row.updated_at = datetime.now(UTC)
            session.commit()
            return True
