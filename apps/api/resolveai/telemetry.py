"""One OpenTelemetry provider with optional local and Langfuse exporters."""

from __future__ import annotations

import os
from threading import Lock

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


_lock = Lock()
_configured = False
_langfuse = None


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def configure_telemetry():
    global _configured, _langfuse
    with _lock:
        if _configured:
            return
        provider = TracerProvider(resource=Resource.create({"service.name": "resolveai-api"}))
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            from langfuse import Langfuse
            from langfuse.span_filter import is_default_export_span
            from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

            def should_export(span):
                scope = span.instrumentation_scope.name if span.instrumentation_scope else ""
                return is_default_export_span(span) or scope == "resolveai.agent"

            def redact(*, params):
                patches = {}
                for identifier, span in params.spans.items():
                    sensitive = tuple(key for key in span.attributes if any(part in key.lower() for part in ("authorization", "token", "address", "payment", "customer_name", "gen_ai.prompt", "gen_ai.completion")))
                    if sensitive:
                        patches[identifier] = OtelSpanPatch(delete_attributes=sensitive, set_attributes={"masking.applied": True})
                return MaskOtelSpansResult(span_patches=patches) if patches else None

            _langfuse = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"], base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), sample_rate=float(os.getenv("LANGFUSE_SAMPLE_RATE", "1")), tracer_provider=provider, should_export_span=should_export, mask_otel_spans=redact)
        trace.set_tracer_provider(provider)
        _configured = True


def tracer():
    return trace.get_tracer("resolveai.agent")


def score(trace_id: str, name: str, value: float):
    if _langfuse is None:
        return False
    try:
        _langfuse.create_score(trace_id=trace_id, name=name, value=value)
        return True
    except Exception:
        return False
