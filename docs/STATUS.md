# ResolveAI implementation status

> Verified 2026-10-02. This page reports repository evidence, not v6 design targets. The approved design remains in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and [v6](../plans/resolveai-v6.md).

## Current snapshot

- Git was initialized; the initial implementation commit is `cd94ed7` and the local Prompt catalog commit is `ed6f457`. [release-v1](../prompts/releases/release-v1.json) locks the catalog commit and every catalog SHA-256. A complete Git bundle was verified and restored to a separate `/tmp` checkout at `cd94ed7`, where PromptRegistry accepted the release. The catalog, not Langfuse Cloud, supplies runtime Prompt text.
- A FastAPI/SQLAlchemy after-sales service, Alembic migrations, a LangGraph parent graph with read-only policy and order specialist subgraphs, a no-key mock mode, OIDC verification code, a Commerce MCP service, a controlled refund worker, Next.js UI, and Compose definitions are present.
- No-key local checks passed: **24 Python tests** (including 1,000 generated refund allocations, offline OIDC checks, multi-turn return slots, deadline alerts, a cross-role HTTP refund workflow, checkpoint turn isolation, and strict model schema validation), **25 unique synthetic smoke cases / 28 executions**, and a **Next.js production build**. A [redacted smoke summary](implementation/mock-smoke-2026-09-29.json) is committed; the full local report is generated under ignored `evals/reports/<run_id>/`.
- The synthetic generator produced and independently validated **100,000** and **1,000,000** orders, with zero violations reported by its CSV validator. The complete 100,000-order world was imported by `COPY` into temporary PostgreSQL 16 in 6.508 seconds; six database quality checks reported zero violations. See the [measured data report](implementation/data-generation-2026-09-29.md) and [PostgreSQL integration report](implementation/postgres-integration-2026-09-30.md). The million-order CSVs remain ignored and were not imported.
- A real PostgreSQL 16.15 server with pgvector 0.6.0 passed clean migrations, a vector operation, the cross-role refund flow and retry, and the 100,000-order import. Docker Compose also ran with PostgreSQL 17.11 and pgvector 0.8.6: migrations, 25-order seed, chat, cross-customer denial, refund ledger, API restart, and OTel→Jaeger trace passed. See the [Docker integration report](implementation/docker-compose-2026-09-30.md). A single live OpenRouter GPT-4o-mini structured-intent request passed after correcting the strict JSON schema; the key remains only in an ignored local file. See the [model integration report](implementation/openrouter-integration-2026-10-02.md). Keycloak sign-in, authenticated MCP calls, Langfuse export, broader real-model evaluations, Mem0 A/B, and load tests remain open.

## Milestones

| v6 stage | Verified now | Still open |
|---|---|---|
| 1. Foundation | Git catalog commit, local release manifest, SQLite and PostgreSQL 16/17 migrations, mock API, ownership tests, 25 demo orders; Docker Compose startup and local Git bundle restore drill | Keycloak/MCP integration; copy bundle to separate offline medium |
| 2. Consistent data | Fixed-seed 100k/1m CSV generation and independent validation; 100k `COPY` into PostgreSQL 16 with zero database violations | Million-order PostgreSQL import and API load report |
| 3. Single graph | Deterministic route/slot baseline, LangGraph graph, safe thread state, optional PostgresSaver wiring, SSE endpoint, one live structured-intent call | Real-model regression, restart/interrupt test, full clarification/Replan contract |
| 4. Policy and business close | Seven-day rule, favorable window, cross-role HTTP return→receipt→inspection→proposal→approval→idempotent ledger test, Docker PostgreSQL refund replay, policy hash publication tests, idempotent due-soon/overdue alerts | Full policy index/version rollback and live Compose reminder delivery |
| 5. Collaboration | Read-only specialist subgraphs, fan-out/fan-in, source/version validation, stale/forged finding tests | Live fault injection and model-assisted specialist analysis |
| 6. Evaluation | 25-case isolated HTTP mock runner with local manifest/JSONL/HTML report | Human-reviewed larger suites, mutation/property tests, locked real-model paired comparison |
| 7. Observation and scale | OTel→Collector→Jaeger seven-span Docker chat trace, optional Langfuse score/mirror/export code; measured CSV generation and 100k PostgreSQL import | Cloud reconciliation/dashboard/quota data, k6/API scale metrics |
| 8. Memory and delivery | Consent/preference correction/deletion/isolation tests, README, web build, Docker image rebuild and PostgreSQL-backed two-turn chat replay | PostgresStore/Mem0 A/B, Playwright browser journeys |

## Reproduction

Follow [README.md](../README.md) for Ubuntu setup, no-key startup, tests, synthetic generation, and optional external configuration. The authority for safety outcomes is the local database ledger and audit, not a model answer or Cloud score.
