# ResolveAI implementation status

> Verified 2026-09-29. This page reports repository evidence, not v6 design targets. The approved design remains in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and [v6](../plans/resolveai-v6.md).

## Current snapshot

- Git was initialized; the implementation is committed as `cd94ed7` and the local Prompt catalog as `ed6f457`. [release-v1](../prompts/releases/release-v1.json) locks the catalog commit and every catalog SHA-256. A complete Git bundle was verified and restored to a separate `/tmp` checkout at `cd94ed7`, where PromptRegistry accepted the release. The catalog, not Langfuse Cloud, supplies runtime Prompt text.
- A FastAPI/SQLAlchemy after-sales service, Alembic migrations, a LangGraph parent graph with read-only policy and order specialist subgraphs, a no-key mock mode, OIDC verification code, a Commerce MCP service, a controlled refund worker, Next.js UI, and Compose definitions are present.
- No-key local checks passed: **21 Python tests** (including 1,000 generated refund allocations, offline OIDC checks, multi-turn return slots, and refund deadline alerts), **25 unique synthetic smoke cases / 28 executions**, and a **Next.js production build**. A [redacted smoke summary](implementation/mock-smoke-2026-09-29.json) is committed; the full local report is generated under ignored `evals/reports/<run_id>/`.
- The synthetic generator produced and independently validated **100,000** and **1,000,000** orders, with zero violations reported by its CSV validator. See [measured data report](implementation/data-generation-2026-09-29.md). The large CSVs are ignored and were not imported into PostgreSQL.
- Docker, live PostgreSQL, Keycloak sign-in, OTel/Jaeger and Langfuse export, OpenRouter real model calls, Mem0 A/B, and load tests have not been run in this environment. See [issues and next gates](implementation/ISSUES.md).

## Milestones

| v6 stage | Verified now | Still open |
|---|---|---|
| 1. Foundation | Git catalog commit, local release manifest, schema migration, mock API, ownership tests, 25 demo orders; local Git bundle restore drill | Docker/Keycloak/MCP integration; copy bundle to separate offline medium |
| 2. Consistent data | Fixed-seed 100k/1m CSV generation and independent validation | PostgreSQL COPY and database quality/load report |
| 3. Single graph | Deterministic route/slot baseline, LangGraph graph, safe thread state, optional PostgresSaver wiring, SSE endpoint | Real-model regression, restart/interrupt test, full clarification/Replan contract |
| 4. Policy and business close | Seven-day rule, favorable window, return→receipt→inspection→proposal→approval→idempotent ledger, policy hash publication tests, idempotent due-soon/overdue alerts | Full policy index/version rollback and live Compose reminder delivery |
| 5. Collaboration | Read-only specialist subgraphs, fan-out/fan-in, source/version validation, stale/forged finding tests | Live fault injection and model-assisted specialist analysis |
| 6. Evaluation | 25-case isolated HTTP mock runner with local manifest/JSONL/HTML report | Human-reviewed larger suites, mutation/property tests, locked real-model paired comparison |
| 7. Observation and scale | OTel adapter/Collector config, optional Langfuse score/mirror/export code; measured CSV generation | Cloud reconciliation/dashboard/quota data, Jaeger trace tests, k6/API scale metrics |
| 8. Memory and delivery | Consent/preference correction/deletion/isolation tests, README and web build | PostgresStore/Mem0 A/B, Playwright, Ubuntu container rebuild proof |

## Reproduction

Follow [README.md](../README.md) for Ubuntu setup, no-key startup, tests, synthetic generation, and optional external configuration. The authority for safety outcomes is the local database ledger and audit, not a model answer or Cloud score.
