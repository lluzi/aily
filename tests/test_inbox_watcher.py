"""Single-folder inbox: registers dropped files and empties the queue."""

from aily.inbox.watcher import WatchedInboxService


class FakeStore:
    def __init__(self) -> None:
        self.queued: list[dict] = []

    async def store_upload(self, **kwargs):
        return {"source_id": "s1", "status": "stored", "duplicate": False}

    async def store_url(self, **kwargs):
        return {"source_id": "s2", "status": "stored", "duplicate": False}

    async def update_status(self, *args, **kwargs):
        return None

    async def enqueue_source_job(self, **kwargs):
        self.queued.append(kwargs)
        return {"job_id": "j1", "job_type": kwargs["job_type"]}


async def test_inbox_registers_and_archives(tmp_path):
    (tmp_path / "deck.pdf").write_bytes(b"%PDF-1.4 hello")
    store = FakeStore()
    watcher = WatchedInboxService(
        source_store=store, inbox_path=tmp_path,
        file_stable_seconds=0.0, archive_processed=True,
    )

    results = await watcher.scan_once()
    assert len(results) == 1
    assert results[0].error is None and results[0].queued
    assert store.queued and store.queued[0]["job_type"] == "process_upload_source"

    # Original moved out of the inbox into .processed; vault stays clean.
    assert not (tmp_path / "deck.pdf").exists()
    assert (tmp_path / ".processed" / "deck.pdf").exists()

    # Archived file is not re-ingested on the next scan.
    assert await watcher.scan_once() == []
