from types import SimpleNamespace

from resolveai.telemetry import mask_cloud_spans, should_export_request, should_export_span


def test_cloud_mask_removes_private_attributes_but_keeps_token_counts():
    attributes = {
        "authorization": "synthetic-secret",
        "access_token": "synthetic-token",
        "gen_ai.prompt.0.content": "private-message",
        "langfuse.observation.input": "private-input",
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 25,
        "prompt_sha256": "catalog-hash",
    }
    result = mask_cloud_spans(params=SimpleNamespace(spans={"span": SimpleNamespace(attributes=attributes)}))
    patch = result.span_patches["span"]
    exported = {key: value for key, value in attributes.items() if key not in patch.delete_attributes}
    exported.update(patch.set_attributes)
    assert exported == {"gen_ai.usage.input_tokens": 120, "gen_ai.usage.output_tokens": 25, "prompt_sha256": "catalog-hash", "masking.applied": True}


def test_cloud_filter_keeps_only_agent_and_explicit_request_spans():
    def span(name, scope="resolveai.agent", **attributes):
        return SimpleNamespace(name=name, attributes=attributes, instrumentation_scope=SimpleNamespace(name=scope))

    assert should_export_span(span("http.request", **{"resolveai.cloud_export": True}))
    assert should_export_span(span("llm.route"))
    assert should_export_span(span("specialist.policy"))
    assert should_export_span(span("agent.request_resources"))
    assert should_export_span(span("commerce.mcp"))
    assert not should_export_span(span("http.request"))
    assert not should_export_span(span("refund.worker"))
    assert not should_export_span(span("refund.deadline_alert"))
    assert not should_export_span(span("GET /orders", "fastapi"))
    assert not should_export_span(span("/chat", "fastapi", **{"http.route": "/chat"}))
    assert not should_export_span(span("SELECT orders", "sqlalchemy"))


def test_cloud_request_export_requires_chat_or_opted_in_evaluation(monkeypatch):
    monkeypatch.delenv("LANGFUSE_EXPORT_EVAL_TRACES", raising=False)
    assert should_export_request("/chat", None, None)
    assert should_export_request("/chat/stream", None, None)
    assert not should_export_request("/health", None, None)
    assert not should_export_request("/returns", "run-1", "case-1")
    monkeypatch.setenv("LANGFUSE_EXPORT_EVAL_TRACES", "1")
    assert should_export_request("/returns", "run-1", "case-1")
    assert not should_export_request("/returns", "run-1", None)
    assert not should_export_request("/returns", None, "case-1")


def test_langfuse_processor_marks_request_root_before_child():
    from langfuse._client.span_processor import LangfuseSpanProcessor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(LangfuseSpanProcessor(public_key="synthetic", secret_key="synthetic", base_url="http://localhost", span_exporter=exporter, should_export_span=should_export_span))
    tracer = provider.get_tracer("resolveai.agent")
    with tracer.start_as_current_span("http.request", attributes={"resolveai.cloud_export": True}):
        with tracer.start_as_current_span("llm.intent"):
            pass
    assert provider.force_flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"http.request", "llm.intent"}
    assert spans["http.request"].attributes["langfuse.internal.is_app_root"] is True
    assert "langfuse.internal.is_app_root" not in spans["llm.intent"].attributes
    provider.shutdown()
