from resolveai.openrouter import strict_json_schema
from resolveai.schemas import RouteDecision


def test_model_span_records_usage_without_prompt_or_key(monkeypatch):
    import httpx
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from resolveai import openrouter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(openrouter, "tracer", lambda: provider.get_tracer("resolveai.agent"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")

    async def post(client, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": '{"route":"knowledge","intents":["policy_qa"],"uncertainty":null}'}}], "usage": {"prompt_tokens": 120, "completion_tokens": 25, "cost": 0.000033}})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = openrouter.call_structured("intent", {"message": "synthetic-private-message"}, RouteDecision)
    assert result.intents == ["policy_qa"]
    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["gen_ai.usage.input_tokens"] == 120
    assert attributes["gen_ai.usage.output_tokens"] == 25
    assert "synthetic-key" not in str(attributes)
    assert "synthetic-private-message" not in str(attributes)
    provider.shutdown()


def test_route_schema_is_compatible_with_strict_output():
    schema = strict_json_schema(RouteDecision)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["route", "intents", "uncertainty"]
    assert "default" not in schema["properties"]["uncertainty"]
    assert schema["properties"]["uncertainty"]["anyOf"][-1] == {"type": "null"}


def test_provider_retry_consumes_shared_attempt_budget(monkeypatch):
    import httpx
    import pytest
    from resolveai import openrouter
    from resolveai.request_budget import BudgetLimits, RequestBudget, budget_scope

    calls = []

    async def post(_client, url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    budget = RequestBudget(BudgetLimits(max_llm_calls=1))
    with budget_scope(budget), pytest.raises(openrouter.ModelUnavailable):
        openrouter.call_structured("intent", {"message": "policy"}, RouteDecision)
    assert len(calls) == 1
    assert calls[0]["provider"]["allow_fallbacks"] is False
    assert budget.snapshot()["llm_attempts"] == 1
    assert budget.snapshot()["exhausted_reason"] == "llm_call_limit"


def test_entire_provider_response_has_cancellable_deadline(monkeypatch):
    import anyio
    import httpx
    import pytest
    import time
    from resolveai import openrouter
    from resolveai.request_budget import BudgetLimits, RequestBudget, budget_scope

    started, cancelled = [], []

    async def post(_client, url, **_kwargs):
        started.append(url)
        try:
            await anyio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    budget = RequestBudget(BudgetLimits(timeout_seconds=0.15))
    start = time.monotonic()
    with budget_scope(budget), pytest.raises(openrouter.ModelUnavailable):
        openrouter.call_structured("intent", {"message": "policy"}, RouteDecision)
    assert time.monotonic() - start < 2
    assert len(started) == len(cancelled) == 1
    assert budget.snapshot()["pending_calls"] == 0
    assert budget.snapshot()["unknown_usage_calls"] == 1


def test_invalid_structured_result_is_charged_without_technical_retry(monkeypatch):
    import httpx
    import pytest
    from resolveai import openrouter
    from resolveai.request_budget import RequestBudget, budget_scope

    calls = []

    async def post(_client, url, **_kwargs):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 25, "cost": 0.000033}})

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    budget = RequestBudget()
    with budget_scope(budget), pytest.raises(openrouter.ModelUnavailable):
        openrouter.call_structured("intent", {"message": "policy"}, RouteDecision)
    assert len(calls) == 1
    assert budget.snapshot()["reported_cost_usd"] == 0.000033
    assert budget.snapshot()["reported_input_tokens"] == 120


def test_billed_technical_error_and_provider_overrun_are_accounted(monkeypatch):
    import httpx
    import pytest
    from resolveai import openrouter
    from resolveai.request_budget import RequestBudget, budget_scope

    calls = []

    async def post(_client, url, **_kwargs):
        calls.append(url)
        return httpx.Response(503, request=httpx.Request("POST", url), json={"usage": {
            "prompt_tokens": 20, "completion_tokens": 0, "cost": 0.01}})

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    budget = RequestBudget()
    with budget_scope(budget), pytest.raises(openrouter.ModelUnavailable):
        openrouter.call_structured("intent", {"message": "policy"}, RouteDecision)
    assert len(calls) == 1
    assert budget.snapshot()["reported_cost_usd"] == 0.01
    assert budget.snapshot()["exhausted_reason"] == "provider_cost_overrun"
