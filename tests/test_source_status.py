"""SourceStatus mapping + the Copilot-facing /api/copilot/sources endpoints."""

from pathlib import Path

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from aily.copilot.router import create_copilot_router, source_status_from_row
from aily.source_store import SourceStore


def test_source_status_mapping_completed():
    row = {
        "source_id": "up:a", "kind": "upload", "sha256": "h", "filename": "d.pdf",
        "normalized_source": "d.pdf", "size_bytes": 1, "status": "completed",
        "metadata": {}, "created_at": "t0", "updated_at": "t1",
    }
    st = source_status_from_row(row, {"markdown_sha256": "m", "package_path": "/p.md"})
    assert st["conversion_status"] == "done"
    assert st["stages"] == {"data": "done", "information": "done", "knowledge": "done"}
    assert st["next_action"] == "none"
    assert st["canonical_markdown_hash"] == "m"
    assert st["artifact_paths"] == ["/p.md"]


def test_source_status_mapping_completed_empty():
    # A source that produced zero notes must read as 'empty/review', not a clean
    # done — and must surface why.
    row = {
        "source_id": "u", "kind": "upload", "status": "completed_empty",
        "metadata": {"empty_reason": "too little extractable text (image-only PDF)"},
        "normalized_source": "deck.pdf", "sha256": "h", "updated_at": "t",
    }
    st = source_status_from_row(row, None)
    assert st["conversion_status"] == "empty"
    assert st["next_action"] == "review"
    assert st["stages"] == {"data": "empty", "information": "empty", "knowledge": "empty"}
    assert "image-only" in st["last_error"]


def test_source_status_mapping_failed():
    row = {
        "source_id": "u", "kind": "upload", "status": "failed",
        "metadata": {"retry_error": "boom"}, "normalized_source": "x",
        "sha256": "h", "updated_at": "t",
    }
    st = source_status_from_row(row, None)
    assert st["conversion_status"] == "failed"
    assert st["next_action"] == "retry"
    assert st["last_error"] == "boom"


async def test_sources_api_list_detail_404_retry(tmp_path):
    store = SourceStore(tmp_path / "s.db", tmp_path / "obj", tmp_path / "md")
    await store.initialize()
    try:
        src = await store.store_upload(
            upload_id="u1", filename="a.pdf", content_type="application/pdf",
            data=b"%PDF-1.4 hello", metadata={},
        )
        sid = src["source_id"]

        async def retry_handler(source_id: str):
            found = await store.get_source(source_id)
            if found is None:
                return {"not_found": True}
            return {"source_id": source_id, "started": True}

        app = FastAPI()
        app.include_router(
            create_copilot_router(
                vault_path=Path(tmp_path),
                source_store=store,
                source_retry_handler=retry_handler,
            )
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listing = await client.get("/api/copilot/sources")
            assert listing.status_code == 200
            assert listing.json()["total"] == 1
            assert listing.json()["sources"][0]["source_id"] == sid

            detail = await client.get(f"/api/copilot/sources/{sid}")
            assert detail.status_code == 200
            assert detail.json()["source_id"] == sid

            missing = await client.get("/api/copilot/sources/does-not-exist")
            assert missing.status_code == 404

            retried = await client.post(f"/api/copilot/sources/{sid}/retry")
            assert retried.status_code == 200
            assert retried.json()["started"] is True
    finally:
        await store.close()
