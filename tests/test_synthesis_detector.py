"""Synthesis detector: pure scoring core + graph-driven detect + threshold gate."""

from aily.synthesis.candidates import SynthesisCandidateStore
from aily.synthesis.detector import SynthesisDetector, subgraphs_to_candidates


def test_subgraphs_to_candidates_filters_by_score_and_size():
    subgraphs = [
        {"anchor_label": "ripe", "node_ids": ["a", "b", "c"], "score": 8.0, "reason": "r", "metrics": {}},
        {"anchor_label": "thin", "node_ids": ["d", "e", "f"], "score": 2.0, "reason": "r", "metrics": {}},  # below score
        {"anchor_label": "small", "node_ids": ["g"], "score": 9.0, "reason": "r", "metrics": {}},  # below min_nodes
    ]
    out = subgraphs_to_candidates(subgraphs, trigger_score=4.0, min_nodes=3, detected_via="daily")
    assert [c.scope_label for c in out] == ["ripe"]
    assert out[0].detected_via == "daily"
    assert out[0].node_ids == ["a", "b", "c"]


class FakeGraph:
    """Minimal graph_db stub for the detector's I/O."""

    def __init__(self, knowledge_count=0):
        self._knowledge = knowledge_count

    async def count_nodes_by_type(self, node_type):
        return self._knowledge if node_type == "knowledge" else 0

    async def get_top_information_nodes_by_semantic_edge_count(self, hours=None, limit=10):
        return [{"id": "i1", "type": "information", "label": "AI strategy",
                 "source": "deck.pdf", "edge_count": 6, "total_weight": 4.0}]

    async def get_neighbors(self, node_id, direction="both", limit=50):
        # Two semantic info neighbors (different sources) + one bookkeeping tag edge (ignored).
        return [
            {"id": "i2", "type": "information", "label": "market", "source": "memo.md",
             "edge": {"relation_type": "depends_on"}},
            {"id": "i3", "type": "information", "label": "moat", "source": "notes.md",
             "edge": {"relation_type": "supports"}},
            {"id": "t1", "type": "information", "label": "tagjunk", "source": "x",
             "edge": {"relation_type": "has_tag"}},  # excluded
        ]


async def test_detect_enqueues_candidate(tmp_path):
    store = SynthesisCandidateStore(tmp_path / "c.db")
    await store.initialize()
    try:
        det = SynthesisDetector(FakeGraph(knowledge_count=20), store, trigger_score=4.0, min_nodes=3)
        summary = await det.detect(detected_via="daily")
        assert summary["created"] == 1, summary
        pending = await store.list(status="pending")
        assert len(pending) == 1
        cand = pending[0]
        # anchor (i1) + 2 semantic neighbors (i2,i3); tag edge excluded.
        assert set(cand["node_ids"]) == {"i1", "i2", "i3"}
        assert cand["scope_label"] == "AI strategy"
        # score = anchor edge_count(6) + sources(3)*0.75 = 8.25
        assert abs(cand["readiness_score"] - 8.25) < 1e-6
    finally:
        await store.close()


async def test_threshold_gate_tracks_knowledge_growth(tmp_path):
    store = SynthesisCandidateStore(tmp_path / "c.db")
    await store.initialize()
    try:
        graph = FakeGraph(knowledge_count=10)
        det = SynthesisDetector(graph, store, trigger_score=4.0)
        # No prior detection -> growth = 10, threshold 15 not met.
        assert await det.should_run_threshold(15) is False
        # Running detect records the current count as the baseline.
        await det.detect(detected_via="threshold")
        assert await det.knowledge_growth_since_last() == 0
        # Graph grows by 16 -> threshold met again.
        graph._knowledge = 26
        assert await det.should_run_threshold(15) is True
    finally:
        await store.close()
