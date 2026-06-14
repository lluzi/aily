"""_terminal_status_for_result: honest completed vs completed_empty."""

from types import SimpleNamespace

import aily.main as m


def _stage(name, items_output, data=None):
    return SimpleNamespace(stage=SimpleNamespace(name=name), items_output=items_output, data=data or {})


def _result(stage_results, final="KNOWLEDGE"):
    return SimpleNamespace(
        pipeline_id="pid",
        final_stage_reached=SimpleNamespace(name=final),
        stage_results=stage_results,
    )


def test_completed_when_notes_produced():
    res = _result([_stage("DATA", 3), _stage("INFORMATION", 3), _stage("KNOWLEDGE", 2)])
    status, meta = m._terminal_status_for_result(res)
    assert status == "completed"
    assert meta["final_stage"] == "KNOWLEDGE"
    assert "empty_reason" not in meta


def test_completed_empty_with_reason_when_quality_gate_rejects():
    data_stage = _stage("DATA", 0, {"quality_assessment": "low", "quality_reason": "Only 1 meaningful paragraphs found"})
    status, meta = m._terminal_status_for_result(_result([data_stage]))
    assert status == "completed_empty"
    assert meta["empty_reason"] == "Only 1 meaningful paragraphs found"


def test_completed_empty_default_reason_when_no_explicit_reason():
    status, meta = m._terminal_status_for_result(_result([_stage("DATA", 0)]))
    assert status == "completed_empty"
    assert "vision model" in meta["empty_reason"]
