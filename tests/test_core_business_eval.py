from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evals.runners import run_core_business


@pytest.mark.parametrize("case", run_core_business.load_cases(), ids=lambda case: case["case_id"])
def test_agent_business_terminal_case(case):
    result = run_core_business.run_case(case, "core-business-test", datetime.now(timezone.utc))
    assert result["status"] == "pass", result
    assert all(result["checks"].values())


def test_scripted_core_scorer_rejects_missing_refund_worker(monkeypatch):
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-partial-quantity-approved")
    monkeypatch.setattr(run_core_business.worker, "issue_approved_once", lambda: [])
    result = run_core_business.run_case(case, "core-no-worker-test", datetime.now(timezone.utc))
    assert result["status"] == "fail"
    assert not result["checks"]["ledger_count"]
    assert not result["checks"]["return_statuses"]
