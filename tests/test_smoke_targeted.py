import json
import sys

import pytest

from evals.runners import run_smoke


def test_targeted_smoke_pass_does_not_claim_full_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(run_smoke, "ROOT", tmp_path)
    monkeypatch.setattr(run_smoke, "run_case", lambda case, mode, run_id: {
        "case_id": case["case_id"], "suite": case["suite"], "split": case["split"],
        "risk_tier": case["risk_tier"], "agent_mode": mode, "status": "pass", "trace_id": None,
    })
    monkeypatch.setattr(sys, "argv", ["run_smoke", "--case-id", "smoke-01", "--run-id", "targeted-test"])
    with pytest.raises(SystemExit) as ended:
        run_smoke.main()
    assert ended.value.code == 0
    summary = json.loads((tmp_path / "evals/reports/targeted-test/summary.json").read_text())
    assert summary["scope"] == "targeted"
    assert summary["selected_case_ids"] == ["smoke-01"]
    assert summary["unique_cases"] == summary["executions"] == 1
    assert summary["targeted_pass"] is True
    assert summary["gate_pass"] is False


def test_targeted_smoke_rejects_unknown_case(monkeypatch, tmp_path):
    monkeypatch.setattr(run_smoke, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_smoke", "--case-id", "not-a-case", "--run-id", "unknown-test"])
    with pytest.raises(SystemExit) as ended:
        run_smoke.main()
    assert ended.value.code == 2
    assert not (tmp_path / "evals/reports/unknown-test").exists()
