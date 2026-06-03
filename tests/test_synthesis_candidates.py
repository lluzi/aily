"""Synthesis candidate queue: dedup, refresh, cooldown, status transitions."""

from aily.synthesis.candidates import (
    SynthesisCandidate,
    SynthesisCandidateStore,
    candidate_id_for_nodes,
)


def _candidate(node_ids, score=5.0, via="daily") -> SynthesisCandidate:
    return SynthesisCandidate(
        candidate_id=candidate_id_for_nodes(node_ids),
        scope_kind="subgraph",
        scope_label="topic X",
        node_ids=node_ids,
        readiness_score=score,
        reason="3 sources, 5 semantic edges",
        metrics={"source_count": 3},
        detected_via=via,
    )


def test_candidate_id_is_order_independent():
    assert candidate_id_for_nodes(["a", "b", "c"]) == candidate_id_for_nodes(["c", "a", "b"])


async def _store(tmp_path) -> SynthesisCandidateStore:
    store = SynthesisCandidateStore(tmp_path / "cand.db")
    await store.initialize()
    return store


async def test_create_then_refresh_dedups(tmp_path):
    store = await _store(tmp_path)
    try:
        r1 = await store.upsert(_candidate(["a", "b", "c"], score=4.0))
        assert r1["action"] == "created"
        # Same scope, higher score -> refresh, not a duplicate row.
        r2 = await store.upsert(_candidate(["a", "b", "c"], score=7.0, via="threshold"))
        assert r2["action"] == "refreshed"
        pending = await store.list(status="pending")
        assert len(pending) == 1
        assert pending[0]["readiness_score"] == 7.0
        assert pending[0]["detected_via"] == "threshold"
    finally:
        await store.close()


async def test_dismissed_candidate_is_in_cooldown(tmp_path):
    store = await _store(tmp_path)
    try:
        cand = _candidate(["x", "y", "z"])
        await store.upsert(cand)
        await store.set_status(cand.candidate_id, "dismissed", cooldown_hours=72)
        # Re-detection during cooldown must not resurrect it.
        again = await store.upsert(cand)
        assert again["action"] == "skipped"
        assert again["candidate"]["status"] == "dismissed"
        assert await store.list(status="pending") == []
    finally:
        await store.close()


async def test_approved_candidate_not_overwritten(tmp_path):
    store = await _store(tmp_path)
    try:
        cand = _candidate(["m", "n", "o"])
        await store.upsert(cand)
        await store.set_status(cand.candidate_id, "approved", workflow_run_id="wf_1")
        again = await store.upsert(cand)
        assert again["action"] == "skipped"
        row = await store.get(cand.candidate_id)
        assert row["status"] == "approved"
        assert row["workflow_run_id"] == "wf_1"
    finally:
        await store.close()


async def test_list_orders_by_readiness(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.upsert(_candidate(["a1", "a2", "a3"], score=3.0))
        await store.upsert(_candidate(["b1", "b2", "b3"], score=9.0))
        await store.upsert(_candidate(["c1", "c2", "c3"], score=6.0))
        scores = [c["readiness_score"] for c in await store.list(status="pending")]
        assert scores == [9.0, 6.0, 3.0]
    finally:
        await store.close()
