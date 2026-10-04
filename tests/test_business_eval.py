from __future__ import annotations

import pytest

from evals.runners import run_business


@pytest.mark.parametrize("case", run_business.load_cases(), ids=lambda case: case["case_id"])
def test_business_terminal_case(case):
    result = run_business.run_case(case, "business-test")
    assert result["status"] == "pass", result
    assert all(result["checks"].values())


def test_business_gate_catches_worker_that_never_issues(monkeypatch):
    case = next(case for case in run_business.load_cases() if case["scenario"] == "approved_refund")
    monkeypatch.setattr(run_business.worker, "issue_approved_once", lambda: [])
    result = run_business.run_case(case, "broken-worker-test")
    assert result["status"] == "fail"
    assert not result["checks"]["ledger_count"]
    assert not result["checks"]["return_statuses"]
