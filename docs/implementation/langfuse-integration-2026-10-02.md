# Langfuse integration check — 2026-10-02

The configured US Cloud project authenticated successfully (`GET /api/public/projects`, HTTP 200). The public/secret project keys and `https://us.cloud.langfuse.com` base URL are in the ignored local `.env`, with mode `600`; the existing OpenRouter key remains present. Docker Compose passes these credentials to API/worker at runtime. Neither credentials nor raw Cloud exports are committed.

## Verified checks

| Check | Measured result |
|---|---|
| Prompt mirror | All six `release-v1` catalog templates mirrored as version 1; `--check` returned `status=ok`, no drift. Repeated sync reused version 1. Runtime text still comes from the local catalog. |
| Synthetic masking canary | One custom span with a fake value in `authorization`, `address`, and `customer_name` reached Langfuse and Jaeger with that value absent. Cloud metadata included `masking.applied`. |
| Live chat | One synthetic Docker `POST /chat` using GPT-4o-mini returned HTTP 200 and `status=clarify`, asking for a return item identifier. No refund was performed. |
| Trace structure | Six spans in each exporter, with the same trace ID. Cloud parents were present for every non-root span. |
| Generation | `llm.intent`, type `GENERATION`, model `openai/gpt-4o-mini-2024-07-18`, 170 input tokens + 19 output tokens = 189 tokens. Provider-reported total cost was USD 0.0000369. |
| Prompt link | Generation linked to `intent_route`, version 1, using the locally stored mirror mapping. |
| Raw content | Cloud observation input/output fields were null. The adapter records usage and provenance without prompt/completion text or credentials. |
| Score ingestion | `setup_telemetry_verified=1` was submitted to the synthetic trace and read back through the scores API. This is a setup check, not an answer-quality score. |
| Local regression suite | `.venv/bin/pytest -q`: 27 passed. New checks cover sensitive attribute removal, usage count retention, health filtering, chat parent retention, and generation metadata without prompt/key content. |

The Langfuse observation filter now retains FastAPI parents and excludes health probes. The custom request middleware skips health spans. Numeric input/output counts survive Cloud masking; raw content attributes and credential-like fields are removed. This follows Langfuse's [masking interface](https://langfuse.com/docs/observability/features/masking) and [token/cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking). The local Collector has a separate named-attribute removal policy; this canary proves removal of the three tested fields, not arbitrary sensitive content in every possible span field.

One repeat sync encountered a transient TLS handshake timeout; a subsequent idempotent retry succeeded with the same six version-1 prompts. Prompt sync creates its output directory when needed. The Compose `observability` volume stores the prompt-version mapping across API recreation. The mapping is metadata, never an inference prompt source. Both Git and Docker build context ignore any host copy of that mapping.

## Reproduce

With credentials already saved in the local `.env`, run the Docker commands in the [README](../../README.md#langfuse-cloud). Open the project's Prompts and Tracing pages. Subsequent UI chats at `http://localhost:3000` will create traces; allow a short delay for batch export and Cloud ingestion. Local traces remain available in Jaeger at `http://localhost:16686`.

This establishes initial Cloud connectivity, prompt mirroring, generation telemetry, masking, and scores. It does not complete the broader v6 evaluation program: full run export/reconciliation, custom dashboards, quota calibration, model comparisons, and load tests remain open. The one-call cost is an observation, not a workload benchmark.
