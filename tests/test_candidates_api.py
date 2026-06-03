"""Copilot candidate API: list / approve (with generation hook) / dismiss / 404."""

from pathlib import Path

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from aily.copilot.router import create_copilot_router
from aily.synthesis.candidates import (
    SynthesisCandidate,
    SynthesisCandidateStore,
    candidate_id_for_nodes,
)


async def test_candidate_api_list_approve_dismiss(tmp_path):
    store = SynthesisCandidateStore(tmp_path / "cand.db")
    await store.initialize()
    try:
        node_ids = ["n1", "n2", "n3"]
        cid = candidate_id_for_nodes(node_ids)
        await store.upsert(
            SynthesisCandidate(
                candidate_id=cid, scope_kind="subgraph", scope_label="topic X",
                node_ids=node_ids, readiness_score=6.0, reason="ripe",
            )
        )

        generated: list[str] = []

        async def generate_handler(candidate_id: str):
            generated.append(candidate_id)
            return {"workflow_run_id": "wf_x", "started": True}

        app = FastAPI()
        app.include_router(
            create_copilot_router(
                vault_path=Path(tmp_path),
                synthesis_store=store,
                candidate_generate_handler=generate_handler,
                candidate_cooldown_hours=72,
            )
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listing = await client.get("/api/copilot/candidates")
            assert listing.status_code == 200
            assert listing.json()["total"] == 1
            assert listing.json()["candidates"][0]["candidate_id"] == cid

            missing = await client.post("/api/copilot/candidates/nope/approve")
            assert missing.status_code == 404

            approved = await client.post(f"/api/copilot/candidates/{cid}/approve")
            assert approved.status_code == 200
            body = approved.json()
            assert body["candidate"]["status"] == "approved"
            assert body["generation"] == {"workflow_run_id": "wf_x", "started": True}
            assert generated == [cid]  # generation hook fired exactly once

            # Approved candidates drop out of the pending list.
            assert (await client.get("/api/copilot/candidates")).json()["total"] == 0

        # A second, dismissable candidate enters cooldown.
        cid2 = candidate_id_for_nodes(["a", "b", "c"])
        await store.upsert(
            SynthesisCandidate(candidate_id=cid2, scope_kind="subgraph", scope_label="Y",
                               node_ids=["a", "b", "c"], readiness_score=5.0)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            dismissed = await client.post(f"/api/copilot/candidates/{cid2}/dismiss")
            assert dismissed.status_code == 200
            assert dismissed.json()["candidate"]["status"] == "dismissed"
            assert dismissed.json()["candidate"]["cooldown_until"] is not None
    finally:
        await store.close()
