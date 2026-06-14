"""_render_status_note: the vault-visible heartbeat / what's-new / needs-me view."""

import aily.main as m


def test_status_note_renders_alive_counts_and_attention():
    note = m._render_status_note({
        "updated": "2026-06-04T10:00:00",
        "counts": {"completed": 3, "processing": 1, "completed_empty": 1, "failed": 0, "queued": 0},
        "recent": [{"title": "deck.pdf", "status": "completed", "updated": "t1"}],
        "attention": ["⚠️ scan.pdf produced no notes — image-only PDF"],
        "candidates": [{"scope_label": "AI agents", "readiness_score": 6.5}],
    })
    assert "🟢 Engine running" in note
    assert "completed: 3" in note and "empty: 1" in note
    assert "deck.pdf — completed" in note
    assert "Needs your attention" in note and "image-only PDF" in note
    assert "Synthesis candidates awaiting approval" in note and "AI agents" in note


def test_status_note_handles_empty_vault():
    note = m._render_status_note({"updated": "t", "counts": {}, "recent": [], "attention": [], "candidates": []})
    assert "nothing yet" in note
    assert "Needs your attention" not in note
