from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .master_data import MasterDataBundle
from .models import (
    DocumentRevision,
    DocumentStatus,
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


class RevisionRow(Base):
    __tablename__ = "document_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
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
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recognition_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
        )
        Base.metadata.create_all(self.engine)

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
    ) -> GameSummary:
        scoresheet_state = "not_uploaded"
        if document is not None:
            if document.status == DocumentStatus.CONFIRMED:
                scoresheet_state = "confirmed"
            elif document.recognition is not None:
                scoresheet_state = "recognized"
            else:
                scoresheet_state = "uploaded"
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
            return [self._game_summary(row, latest.get(row.id)) for row in rows]

    def get_game(self, game_id: str) -> GameDetail:
        with Session(self.engine) as session:
            row = session.get(GameRow, game_id)
            if row is None:
                raise GameNotFoundError(game_id)
            summary = self._game_summary(row, self._latest_game_documents(session).get(row.id))
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
            session.add(
                RevisionRow(
                    document_id=document.id,
                    revision=0,
                    source=source,
                    payload=payload,
                    created_at=now,
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
            if row.revision != base_revision:
                raise RevisionConflictError(base_revision, row.revision)

            previous = ScoresheetDocument.model_validate_json(row.payload)
            now = datetime.now(UTC)
            document.id = document_id
            document.revision = row.revision + 1
            document.created_at = previous.created_at
            document.updated_at = now
            payload = document.model_dump_json()
            row.revision = document.revision
            row.status = document.status.value
            row.payload = payload
            row.updated_at = now
            session.add(
                RevisionRow(
                    document_id=document_id,
                    revision=document.revision,
                    source=source,
                    payload=payload,
                    created_at=now,
                )
            )
            session.commit()
        return document

    def revisions(self, document_id: str) -> list[DocumentRevision]:
        with Session(self.engine) as session:
            exists = session.get(DocumentRow, document_id)
            if exists is None:
                raise DocumentNotFoundError(document_id)
            rows = session.scalars(
                select(RevisionRow)
                .where(RevisionRow.document_id == document_id)
                .order_by(RevisionRow.revision.desc())
            ).all()
            return [
                DocumentRevision(
                    document_id=row.document_id,
                    revision=row.revision,
                    source=row.source,
                    created_at=row.created_at,
                    document=ScoresheetDocument.model_validate_json(row.payload),
                )
                for row in rows
            ]

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

    def create_recognition_run(
        self,
        *,
        run_id: str,
        document_id: str,
        base_revision: int,
        model: str,
        cache_key: str,
        prompt_version: str,
        cached_result: dict | None = None,
    ) -> RecognitionRun:
        now = datetime.now(UTC)
        row = RecognitionRunRow(
            id=run_id,
            document_id=document_id,
            base_revision=base_revision,
            status="succeeded" if cached_result is not None else "pending",
            model=model,
            cache_key=cache_key,
            prompt_version=prompt_version,
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
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)

    def get_recognition_run(self, run_id: str) -> RecognitionRun:
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
            return self._recognition_run(row)

    def mark_recognition_status(self, run_id: str, status: str) -> None:
        if status not in {"connecting", "thinking", "structuring", "validating"}:
            raise ValueError(f"Unsupported recognition status: {status}")
        with Session(self.engine) as session:
            row = session.get(RecognitionRunRow, run_id)
            if row is None:
                raise RecognitionRunNotFoundError(run_id)
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
            row.status = "succeeded" if finalize else "validating"
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
            row.status = "succeeded"
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
            row.status = "failed"
            row.error = message
            if usage is not None:
                row.usage_payload = usage.model_dump_json()
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._recognition_run(row)
