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

    def post(client, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": '{"route":"knowledge","intents":["policy_qa"],"uncertainty":null}'}}], "usage": {"prompt_tokens": 120, "completion_tokens": 25, "cost": 0.000033}})

    monkeypatch.setattr(httpx.Client, "post", post)
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
