from types import SimpleNamespace

from resolveai.telemetry import mask_cloud_spans, should_export_span


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


def test_cloud_filter_keeps_chat_parents_and_excludes_health_checks():
    def span(scope, **attributes):
        return SimpleNamespace(attributes=attributes, instrumentation_scope=SimpleNamespace(name=scope))

    assert not should_export_span(span("fastapi", **{"http.route": "/health"}))
    assert not should_export_span(span("fastapi", **{"code.function.name": "resolveai.api.health"}))
    assert should_export_span(span("fastapi", **{"http.route": "/chat"}))
    assert should_export_span(span("fastapi", **{"code.function.name": "resolveai.api.chat"}))
    assert should_export_span(span("resolveai.agent"))
    assert not should_export_span(span("sqlalchemy"))
