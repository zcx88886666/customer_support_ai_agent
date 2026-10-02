# ResolveAI

ResolveAI is a synthetic, single-company e-commerce after-sales demonstration. It offers a no-key local mock path for customer order lookup, read-only policy and logistics specialists, confirmed return requests, warehouse inspection, supervisor approval, and one idempotent simulated refund. It does not connect to a real payment provider. PostgreSQL with pgvector is the v6 primary database; SQLite is the lightweight fallback for local mock tests. The [v6 specification](plans/resolveai-v6.md) describes the full target; [verified status](docs/STATUS.md) distinguishes implemented work from remaining integration work.

## Docker Compose run (recommended)

From the repository root, start the local mock stack with PostgreSQL 17, pgvector, API, web UI, refund worker, Commerce MCP, OTel Collector, and Jaeger. No API keys are needed:

```bash
docker compose -f infra/compose/compose.yaml up --build -d
docker compose -f infra/compose/compose.yaml ps
docker compose -f infra/compose/compose.yaml exec -T api python scripts/verify_postgres.py --expected-orders 25
```

The API runs Alembic migrations and seeds 25 synthetic orders on first start. Repeated starts keep the existing PostgreSQL volume and demo data. Open `http://localhost:3000` for the mock role workflow (`cust-01` as `customer`, then `warehouse-01` as `warehouse` and `supervisor-01` as `supervisor`). API docs are at `http://localhost:8000/docs`; Jaeger is at `http://localhost:16686`. The mock identity controls are for local loopback use only. The Commerce MCP container starts but requires Keycloak-issued JWTs, so its tools are unavailable in the default mock mode.

To replay the synthetic approval-to-refund workflow, inspect logs, or stop the stack while retaining its data:

```bash
docker compose -f infra/compose/compose.yaml exec -T api python scripts/demo_workflow.py
docker compose -f infra/compose/compose.yaml logs --tail=100 api worker
docker compose -f infra/compose/compose.yaml down
```

The [Docker integration report](docs/implementation/docker-compose-2026-09-30.md) records the actual PostgreSQL 17, chat, refund, restart, and Jaeger checks. A separate [PostgreSQL 16 report](docs/implementation/postgres-integration-2026-09-30.md) covers the 100,000-order import. Set `DATABASE_URL` to a PostgreSQL URL to run API and worker against another local PostgreSQL installation.

An isolated million-order PostgreSQL import and 20/50/100-user no-key k6 read/chat load were measured without changing the live demo database. The [scale report](docs/implementation/million-order-load-2026-10-02.md) has the commands, actual P95s, error rates, resource snapshots, and limits; the load script is [million_orders.js](evals/load/million_orders.js).

PostgreSQL startup also builds the active policy's version-scoped full-text and pgvector search rows. Verify the index, bundle isolation, and stale-index rejection with `docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/verify_policy_search.py`. This is a local character-gram vector baseline; the [verification report](docs/implementation/policy-search-2026-10-02.md) records its limits.

A supervisor can restore a superseded policy through `POST /policies/{bundle_id}/rollback`. The service checks its content hash and search index before switching the active bundle and records an audit event. The [rollback report](docs/implementation/policy-rollback-2026-10-02.md) covers the migrated demo bundle and measured checks.

## Ubuntu setup and no-key mock run

Tested here with Python 3.12.3. Ubuntu 24.04 with Python 3.12, `uv`, and Node 22 is the intended local setup. The following SQLite commands provide a quick mock fallback without PostgreSQL or Docker:

```bash
uv venv .venv
UV_CACHE_DIR=/tmp/resolveai-uv-cache uv sync --locked --extra dev --extra observability
export DATABASE_URL=sqlite:///./resolveai-dev.db
export AUTH_MODE=mock
.venv/bin/alembic upgrade head
.venv/bin/python -m resolveai.seed --clock "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
.venv/bin/uvicorn resolveai.api:app --host 127.0.0.1 --port 8000
```

The mock API requires both `X-Mock-Actor` and `X-Mock-Role`. Try `cust-01` and `customer` at [the local API docs](http://127.0.0.1:8000/docs). `cust-02` owns demo orders 05, 10, 15, 20, and 25. Mock identity is only for loopback development; set `AUTH_MODE=oidc` and use Keycloak for a shared environment. The API verifies JWT signature, issuer, audience, expiry, role, and customer mapping. The read-only Commerce MCP service also verifies the JWT and order ownership.

The web UI is optional for the mock path:

```bash
cd apps/web
npm ci
npm run dev
```

Open `http://localhost:3000`. The page exposes the customer, warehouse, and supervisor steps. Support ticket assignment and policy publication are available in the API docs. In mock mode the UI actor and role inputs are development controls, not real authentication.

## Business workflow

1. Customer: `GET /orders`, `GET /orders/{id}/shipments`, and `POST /chat` with a stable `thread_id`. Use `agent_mode=single` or `collab` to compare read-only evidence routing. The response includes `trace_id` and structured specialist findings.
2. Customer: `POST /returns` with an owned order/item, positive quantity, reason, `confirmed=true`, and an idempotency key. The service recalculates eligibility using the Shanghai business calendar.
3. Warehouse: `POST /warehouse/returns/{id}/receipt`, then `/inspection`, then `POST /returns/{id}/proposal`.
4. Supervisor: `GET /supervisor/proposals`, then `POST /supervisor/proposals/{id}/decision`.
5. Controlled worker: run `.venv/bin/python -m resolveai.worker` for the direct local setup. Only approved, current proposals are issued; retries use `refund:{proposal_id}` and create at most one ledger entry. The same one-shot worker records one warning in the final 24 hours of the seven-day period after receipt and one overdue alert. Supervisors can view them at `GET /supervisor/refund-deadlines`. Compose calls the worker every 30 seconds; the worker ran in the verified Docker stack. The customer and Agent APIs have no refund issuance endpoint.

For a full mock HTTP replay on the seeded local database, run `.venv/bin/python scripts/demo_workflow.py`. It is safe to rerun with the same idempotency key; a second run must not add another ledger entry.

An approval can become stale if relevant order facts change. Calling the proposal endpoint again after such a change marks the old proposal stale and creates a new one requiring a new approval. All amounts are integer CNY cents calculated from the recorded paid allocation. The system does not automatically decide damaged goods, delivery disputes, complex payment splits, or exceptions to the demonstrated return policy.

## Verification

```bash
.venv/bin/pytest -q
.venv/bin/python evals/runners/run_smoke.py
cd apps/web && npm run build
```

The smoke runner validates 25 fixed JSONL cases and runs the collaboration cases in both modes, each against a fresh SQLite database through the authenticated HTTP API. It writes `manifest.json`, `case_results.jsonl`, `summary.json`, and `report.html` under ignored `evals/reports/<run_id>/`. `summary.json` is the local machine-readable gate. These cases are synthetic development cases, not a human-reviewed locked benchmark or a measured real-model comparison.

To regenerate and independently validate large synthetic CSV worlds:

```bash
.venv/bin/python data/generator/generate.py --profile realistic --output data/generated/realistic-100k
.venv/bin/python data/generator/validate.py data/generated/realistic-100k
.venv/bin/python data/generator/generate.py --profile scale --output data/generated/scale-1m
.venv/bin/python data/generator/validate.py data/generated/scale-1m
```

The ignored CSV outputs are rebuilt from a fixed seed and clock. [Measured generation results](docs/implementation/data-generation-2026-09-29.md) include row counts, time, machine, and independent validation results. `data/generator/import_postgres.py` streams validated CSV files with PostgreSQL `COPY` after the demo policy has been seeded. The complete `realistic-100k-v3` world was imported into a temporary PostgreSQL 16 database and passed `scripts/verify_postgres.py`; see the [integration report](docs/implementation/postgres-integration-2026-09-30.md).

## Containers, OIDC, and external integrations

The [Compose file](infra/compose/compose.yaml) binds browser-facing ports to localhost. The optional `oidc` profile imports a Keycloak realm with customer, support, warehouse, and supervisor roles. The browser uses authorization code with PKCE; the local setup and live verification commands follow.

```bash
.venv/bin/python scripts/configure_keycloak.py --prepare
docker compose --env-file .env -f infra/compose/compose.yaml --profile oidc up -d --build keycloak api mcp web
.venv/bin/python scripts/configure_keycloak.py
.venv/bin/python scripts/verify_oidc.py
```

These commands create five synthetic local accounts and store their generated passwords in ignored `.local/demo-accounts.json` (mode `600`). The prepare command sets `AUTH_MODE=oidc` in ignored `.env` while preserving existing OpenRouter/Langfuse entries. Open `http://localhost:3000` for browser sign-in. Keep the hostname as `localhost` for both web and Keycloak; using `127.0.0.1` for one of them breaks the browser OIDC session. `verify_oidc.py` exercises authorization-code login with PKCE, customer ownership, role denial, and authenticated MCP reads. The MCP audience is `http://localhost:8001/mcp`, distinct from the API audience. The graph passes the signed customer token only to the read-only MCP client at runtime; no token is written to checkpoints.

The browser suite runs with `cd apps/web && npm run test:e2e` after installing Playwright Chromium, or with `CHROME_TEST_PATH` pointing at an existing Chrome binary. It covers customer, support, warehouse, and supervisor sign-in. The test reads only the ignored local account file.

The local [Prompt catalog](prompts/catalog/) is the only editable Prompt source. [release-v1](prompts/releases/release-v1.json) locks its hashes to the committed catalog; `PromptRegistry` refuses a hash mismatch at startup. `scripts/release_prompts.py` makes a new manifest only after a catalog commit. `scripts/sync_prompts_to_langfuse.py release-v1` mirrors the exact local content to Langfuse when keys are configured; `--check` detects a missing or drifted release label. Cloud Prompt text is never loaded for inference. The mock path does not need Langfuse keys.

Set `OPENROUTER_API_KEY` to enable structured intent extraction and bounded specialist evidence review using the exact model ID in [the model registry](packages/agent/models-mock-v1.json). For Docker Compose, put `OPENROUTER_API_KEY=...` in a repository-root `.env` file (ignored by Git) with mode `600` and start with `docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api`. Do not paste the key into chat or command arguments. The deterministic mock remains the no-key path. One [live structured-intent request](docs/implementation/openrouter-integration-2026-10-02.md) and a separate two-specialist evidence review passed on GPT-4o-mini; broader model quality and cost benchmarks remain open.

### Langfuse Cloud

Add `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL=https://us.cloud.langfuse.com` to the same local `.env` for the configured US project. The project keys identify the project; no project name is needed in the application. Keep the existing OpenRouter key. Then run:

```bash
docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api worker
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/sync_prompts_to_langfuse.py release-v1
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/sync_prompts_to_langfuse.py release-v1 --check
```

Open the project in Langfuse: **Prompts** contains six mirrored templates and **Tracing** contains subsequent chat traces. Generations show the configured model, provider-reported token usage/cost, and the mirrored prompt version. Inference still reads the hash-verified local catalog. Rerun sync when publishing a new local prompt release. The Compose `observability` volume preserves the version mapping across API container recreation; removing that volume requires another sync.

Health probes are excluded from Cloud export. Cloud masking removes named sensitive attributes and prompt/completion content while retaining numeric usage counts. Local OTLP export uses `OTEL_EXPORTER_OTLP_ENDPOINT`; the Collector separately removes named sensitive attributes before Jaeger. A [live integration check](docs/implementation/langfuse-integration-2026-10-02.md) verified both exporters, a masking canary, prompt links, generation usage/cost, and score ingestion. Raw chat text is not included in the generation spans. To reconcile an evaluation run after Cloud ingestion, use `scripts/export_langfuse_run.py <run_id>`; one [28-execution synthetic smoke run](docs/implementation/langfuse-eval-reconciliation-2026-10-02.md) reconciled all traces, case links, and scores. Larger locked-run reconciliation, custom dashboards, and quota calibration remain open.

## Security and backups

Do not commit `.env`, access tokens, real customer data, raw private conversations, generated million-row CSV files, or unredacted traces. The repository ignores local databases, reports, large data, and export folders. Create a complete bundle and test a restore before copying it to a separate offline medium:

```bash
git bundle create /tmp/resolveai-backup.bundle --all
git bundle verify /tmp/resolveai-backup.bundle
git clone /tmp/resolveai-backup.bundle /tmp/resolveai-restore
sha256sum /tmp/resolveai-backup.bundle
```

In the restored checkout, compare `git rev-parse HEAD` with the source and load `release-v1` through `PromptRegistry` to verify catalog hashes. A local bundle alone is not an offline backup; copy it to storage outside this machine.

The remaining v6 gates, failed commands, and external prerequisites are tracked in [implementation issues](docs/implementation/ISSUES.md) and [status](docs/STATUS.md). The project should not be described as production deployed or fully validated while those gates remain open.
