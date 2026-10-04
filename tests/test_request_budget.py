from concurrent.futures import ThreadPoolExecutor

import pytest

from resolveai.request_budget import BudgetExceeded, BudgetLimits, RequestBudget, budget_scope, current_budget


def test_parallel_attempts_share_hard_ten_call_limit():
    budget = RequestBudget(BudgetLimits(max_llm_calls=10, max_tokens=10000, max_cost_usd=1))

    def reserve(_):
        try:
            return budget.reserve(100, 50, 0.01)
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        tickets = list(pool.map(reserve, range(12)))
    assert sum(ticket is not None for ticket in tickets) == 10
    assert budget.snapshot()["llm_attempts"] == 10
    assert budget.snapshot()["exhausted_reason"] == "llm_call_limit"
    with pytest.raises(ValueError):
        BudgetLimits(max_llm_calls=11)


def test_parallel_reservations_cannot_oversubscribe_tokens_or_cost():
    for limits, reason in ((BudgetLimits(max_tokens=149), "token_limit"),
                           (BudgetLimits(max_cost_usd=0.009), "cost_limit")):
        budget = RequestBudget(limits)
        with pytest.raises(BudgetExceeded):
            budget.reserve(100, 50, 0.01)
        assert budget.snapshot()["llm_attempts"] == 0
        assert budget.snapshot()["exhausted_reason"] == reason


def test_usage_reconciles_reservations_and_failed_call_keeps_unknown_spend():
    budget = RequestBudget(BudgetLimits(max_tokens=1000, max_cost_usd=0.02))
    first = budget.reserve(200, 100, 0.01)
    second = budget.reserve(200, 100, 0.01)
    budget.finish(first, {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001})
    budget.finish(second, {})
    summary = budget.snapshot()
    assert summary["reported_input_tokens"] == 100
    assert summary["reported_output_tokens"] == 20
    assert summary["reported_cost_usd"] == 0.001
    assert summary["accounted_tokens"] == 420
    assert summary["accounted_cost_usd"] == pytest.approx(0.011)
    assert summary["unknown_usage_calls"] == 1


def test_request_context_is_restored_and_deadline_uses_monotonic_time():
    clock = [100.0]
    budget = RequestBudget(BudgetLimits(timeout_seconds=2), clock=lambda: clock[0])
    assert current_budget() is None
    with budget_scope(budget):
        assert current_budget() is budget
        clock[0] = 102.0
        with pytest.raises(BudgetExceeded):
            budget.reserve(10, 10, 0.001)
    assert current_budget() is None
    assert budget.snapshot()["exhausted_reason"] == "deadline_expired"


def test_provider_overrun_stops_further_calls_without_hiding_reported_usage():
    budget = RequestBudget(BudgetLimits(max_cost_usd=0.01))
    ticket = budget.reserve(100, 100, 0.001)
    budget.finish(ticket, {"prompt_tokens": 110, "completion_tokens": 30, "cost": 0.02})
    assert budget.snapshot()["reported_cost_usd"] == 0.02
    assert budget.snapshot()["exhausted_reason"] == "provider_cost_overrun"
    with pytest.raises(BudgetExceeded):
        budget.reserve(1, 1, 0.000001)


def test_api_rejects_invalid_budget_configuration_at_startup(monkeypatch):
    import anyio
    from resolveai.api import app, lifespan

    monkeypatch.setenv("AGENT_MAX_LLM_CALLS", "11")

    async def startup():
        async with lifespan(app):
            pass

    with pytest.raises(ValueError, match="1–10"):
        anyio.run(startup)
