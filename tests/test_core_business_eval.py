from __future__ import annotations

from datetime import datetime, timezone

from evals.runners import run_core_business


def test_agent_return_continues_through_approved_refund():
    case = run_core_business.load_cases()[0]
    result = run_core_business.run_case(case, "core-business-test", datetime.now(timezone.utc))
    assert result["status"] == "pass", result
    assert result["checks"]["chat_committed_return"]
    assert result["checks"]["chat_no_early_refund"]
    assert result["checks"]["refund_authorized"]
    assert result["checks"]["worker_replay"]
