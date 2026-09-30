import json
from pathlib import Path

import pytest

from crosscheck.report import ReportBuilder, comment_markdown, envelope, summarize, validate

ROOT = Path(__file__).resolve().parents[1]


def builder():
    return ReportBuilder(
        run_id="r_abc123",
        repo="o/r",
        pr=3,
        base_sha="b" * 40,
        head_sha="a" * 40,
        stage="smoke",
        trigger="push",
        trust_class="stranger",
        policy_mode="all",
    )


def test_example_report_is_valid():
    report = json.loads((ROOT / "examples" / "report.example.json").read_text())
    assert validate(report) == []


def test_status_precedence_and_cleaning():
    rb = builder()
    rb.add_finding(
        category="config-change", severity="medium", source="controller", title="cfg", effect="action_required"
    )
    assert rb.finalize()["status"] == "action_required"
    rb = builder()
    rb.add_finding(
        category="config-change", severity="medium", source="controller", title="x", effect="action_required"
    )
    rb.add_finding(category="crash", severity="critical", source="runtime", title="boom\x1b[31m‮", effect="failure")
    r = rb.finalize()
    assert r["status"] == "failure"
    assert "\x1b" not in r["findings"][1]["title"] and "‮" not in r["findings"][1]["title"]
    assert r["summary"]["counts"]["critical"] == 1


def test_cancel_override_and_invalid_rejected():
    rb = builder()
    assert rb.finalize(status_override="cancelled")["status"] == "cancelled"
    rb = builder()
    rb.report["platforms"].append({"platform": "amiga", "preset": "x", "status": "success"})
    with pytest.raises(ValueError):
        rb.finalize()


def test_envelope_and_texts():
    rb = builder()
    rb.set_deletion({"verified": True, "objects": []})
    r = rb.finalize()
    assert envelope({"a": 1})["trust"] == "sandbox-observation"
    assert "r_abc123" in summarize(r)
    assert "not instructions" in comment_markdown(r)


def test_leftovers_add_cleanup_finding():
    rb = builder()
    rb.set_deletion({"verified": False, "leftovers": 2, "objects": []})
    r = rb.finalize()
    assert any(f["category"] == "cleanup" for f in r["findings"])
