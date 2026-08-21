from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock

from sqlalchemy import text

from scoresheet_reader.database import DocumentRepository, RevisionConflictError
from scoresheet_reader.recognition import RecognitionQueue

from .synthetic_fixture import synthetic_document


def test_revision_compare_and_swap_allows_only_one_concurrent_writer(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "race.sqlite3")
    created = repository.create(synthetic_document("race-document"), source="test")
    barrier = Barrier(2)

    def update_document(game_number: str) -> str:
        candidate = created.model_copy(deep=True)
        candidate.header.game_number = game_number
        barrier.wait()
        try:
            repository.update(candidate.id, 0, candidate, "human")
        except RevisionConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update_document, ["A", "B"]))

    assert sorted(outcomes) == ["conflict", "saved"]
    assert repository.get(created.id).revision == 1
    change_log = repository.changes(created.id)
    assert len(change_log.items) == 1
    assert change_log.items[0].action == "human_edit"
    assert change_log.items[0].changes[0].path == "/header/game_number"


def test_change_log_paginates_without_returning_document_snapshots(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "changes.sqlite3")
    document = repository.create(synthetic_document("change-document"), source="test")
    for index in range(3):
        candidate = repository.get(document.id)
        candidate.header.game_number = str(index + 1)
        repository.update(document.id, candidate.revision, candidate, "human")

    first = repository.changes(document.id, limit=2)
    assert len(first.items) == 2
    assert first.next_before_id == first.items[-1].id
    second = repository.changes(document.id, limit=2, before_id=first.next_before_id)
    assert len(second.items) == 1
    assert second.next_before_id is None
    assert first.items[0].model_dump().keys() == {
        "id",
        "document_id",
        "action",
        "summary",
        "changes",
        "created_at",
    }


def test_legacy_snapshots_migrate_once_then_the_table_is_deleted(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    repository = DocumentRepository(database_path)
    initial = repository.create(synthetic_document("legacy-document"), source="test")
    updated = initial.model_copy(deep=True)
    updated.header.game_number = "M-12"
    saved = repository.update(initial.id, 0, updated, "human")

    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM document_change_logs"))
        connection.execute(
            text(
                "CREATE TABLE document_revisions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, document_id VARCHAR(64) NOT NULL, "
                "revision INTEGER NOT NULL, source VARCHAR(32) NOT NULL, "
                "payload TEXT NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO document_revisions"
                "(document_id, revision, source, payload, created_at) "
                "VALUES (:document_id, :revision, :source, :payload, :created_at)"
            ),
            [
                {
                    "document_id": initial.id,
                    "revision": 0,
                    "source": "game_upload",
                    "payload": initial.model_dump_json(),
                    "created_at": datetime.now(UTC).isoformat(),
                },
                {
                    "document_id": saved.id,
                    "revision": 1,
                    "source": "human",
                    "payload": saved.model_dump_json(),
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ],
        )
        connection.execute(text("DELETE FROM master_data_state WHERE key = 'change_log_schema'"))

    migrated = DocumentRepository(database_path)
    assert migrated.get(initial.id).header.game_number == "M-12"
    assert len(migrated.changes(initial.id).items) == 1
    with migrated.engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'document_revisions'"
                )
            ).first()
            is None
        )

    reopened = DocumentRepository(database_path)
    assert len(reopened.changes(initial.id).items) == 1


def test_persistent_queue_honors_the_configured_concurrency_and_fifo_order(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "queue.sqlite3")
    run_ids: list[str] = []
    for index in range(4):
        document = repository.create(
            synthetic_document(f"queue-document-{index}"),
            source="test",
        )
        run, _ = repository.create_recognition_run(
            run_id=f"queue-run-{index}",
            document_id=document.id,
            base_revision=0,
            model="mock",
            cache_key=f"cache-{index}",
            prompt_version="test",
            trigger="upload",
        )
        run_ids.append(run.id)

    class RecordingService:
        def __init__(self) -> None:
            self.lock = Lock()
            self.active = 0
            self.maximum_active = 0
            self.started: list[str] = []

        def execute(self, run_id: str) -> str:
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                self.started.append(run_id)
            time.sleep(0.04)
            repository.fail_recognition(run_id, "mock terminal state")
            with self.lock:
                self.active -= 1
            return "completed"

    service = RecordingService()
    queue = RecognitionQueue(repository, service, concurrency=2)  # type: ignore[arg-type]
    queue.start()
    try:
        for _ in range(100):
            if all(repository.get_recognition_run(run_id).status == "failed" for run_id in run_ids):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("queued recognitions did not finish")
    finally:
        queue.stop()

    assert service.maximum_active == 2
    assert set(service.started[:2]) == {"queue-run-0", "queue-run-1"}
    assert set(service.started[2:]) == {"queue-run-2", "queue-run-3"}


def test_startup_recovery_keeps_pending_tasks_and_interrupts_inflight_tasks(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "recovery.sqlite3")
    document = repository.create(synthetic_document("recovery-document"), source="test")
    pending, _ = repository.create_recognition_run(
        run_id="pending-run",
        document_id=document.id,
        base_revision=0,
        model="mock",
        cache_key="pending-cache",
        prompt_version="test",
    )
    inflight, _ = repository.create_recognition_run(
        run_id="inflight-run",
        document_id=document.id,
        base_revision=0,
        model="mock",
        cache_key="inflight-cache",
        prompt_version="test",
    )
    repository.mark_recognition_status(inflight.id, "connecting")

    assert repository.recover_interrupted_recognition_runs() == 1
    assert repository.get_recognition_run(pending.id).status == "pending"
    recovered = repository.get_recognition_run(inflight.id)
    assert recovered.status == "interrupted"
    assert "避免重复付费" in recovered.error


def test_rate_limit_requeues_once_and_reduces_future_work_to_serial(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "rate-limit.sqlite3")
    document = repository.create(synthetic_document("rate-limit-document"), source="test")
    run, _ = repository.create_recognition_run(
        run_id="rate-limited-run",
        document_id=document.id,
        base_revision=0,
        model="mock",
        cache_key="rate-limit-cache",
        prompt_version="test",
    )

    class RateLimitedOnceService:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, run_id: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return "rate_limited"
            repository.fail_recognition(run_id, "mock terminal state")
            return "completed"

    service = RateLimitedOnceService()
    queue = RecognitionQueue(repository, service, concurrency=2)  # type: ignore[arg-type]
    queue.start()
    try:
        for _ in range(100):
            if repository.get_recognition_run(run.id).status == "failed":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("rate-limited recognition did not finish")
    finally:
        queue.stop()

    assert service.calls == 2
    assert queue.effective_concurrency == 1
    assert repository.get_recognition_run(run.id).retry_count == 1
