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

For overlapping refund worker and pre-commit rollback recovery checks, use a fresh isolated PostgreSQL database and the commands in the [worker recovery report](docs/implementation/refund-worker-recovery-2026-10-03.md).

An isolated [worker container restart drill](docs/implementation/worker-container-restart-2026-10-03.md) also force-killed the worker after one committed refund; a new container issued nothing further and the database retained one correct ledger and audit event.

An isolated million-order PostgreSQL import and 20/50/100-user no-key k6 read/chat load were measured without changing the live demo database. The [scale report](docs/implementation/million-order-load-2026-10-02.md) has the commands, actual P95s, error rates, resource snapshots, and limits; the load script is [million_orders.js](evals/load/million_orders.js).

PostgreSQL startup also builds the active policy's version-scoped full-text and pgvector search rows. Verify the index, bundle isolation, and stale-index rejection with `docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/verify_policy_search.py`. This is a local character-gram vector baseline; the [verification report](docs/implementation/policy-search-2026-10-02.md) records its limits.

A supervisor can restore a superseded policy through `POST /policies/{bundle_id}/rollback`. The service checks its content hash and search index before switching the active bundle and records an audit event. The [rollback report](docs/implementation/policy-rollback-2026-10-02.md) covers the migrated demo bundle and measured checks.

An optional [offline embedding benchmark](docs/implementation/policy-embedding-2026-10-03.md) compares the current character-gram retrieval with two local FastEmbed models. Install `fastembed==0.8.1` in a separate virtual environment, then run `FASTEMBED_PYTHON=/path/to/fastembed-venv/bin/python HF_HOME=/path/to/model-cache .venv/bin/python evals/runners/run_policy_embedding.py`. The semantic candidates are experimental and are not part of the API runtime.

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

When a specialist reports a conflict, the coordinator retries at most twice and reuses unaffected evidence only after checking it against current database facts. The isolated [PostgreSQL conflict report](docs/implementation/specialist-conflict-2026-10-03.md) records the real checkpoint run and its limits.

The web UI is optional for the mock path:

```bash
cd apps/web
npm ci
npm run dev
```

Open `http://localhost:3000`. The page exposes the customer, warehouse, and supervisor steps. Support ticket assignment and policy publication are available in the API docs. In mock mode the UI actor and role inputs are development controls, not real authentication.

## Business workflow

1. Customer: `GET /orders`, `GET /orders/{id}/shipments`, and `POST /chat` with a stable `thread_id`. Use `agent_mode=single` or `collab` to compare read-only evidence routing. The response includes `trace_id` and structured specialist findings.
   For an order with multiple packages, chat asks the customer to select a `shipment_id` before giving logistics facts. The web UI provides a package selector; the [package selection report](docs/implementation/package-selection-2026-10-03.md) records the isolated OIDC/MCP browser check and the current split-delivery return limit.
2. Customer: `POST /returns` with an owned order/item, positive quantity, reason, `confirmed=true`, and an idempotency key. The service recalculates eligibility using the Shanghai business calendar.
3. Warehouse: `POST /warehouse/returns/{id}/receipt`, then `/inspection`, then `POST /returns/{id}/proposal`.
4. Supervisor: `GET /supervisor/proposals`, then `POST /supervisor/proposals/{id}/decision`.
5. Controlled worker: run `.venv/bin/python -m resolveai.worker` for the direct local setup. Only approved, current proposals are issued; retries use `refund:{proposal_id}` and create at most one ledger entry. The same one-shot worker records one warning in the final 24 hours of the seven-day period after receipt and one overdue alert. Supervisors can view them at `GET /supervisor/refund-deadlines`. Compose calls the worker every 30 seconds; the worker ran in the verified Docker stack. The customer and Agent APIs have no refund issuance endpoint.

An isolated [live worker deadline check](docs/implementation/deadline-delivery-2026-10-03.md) verified that the 30-second loop created one due-soon and one overdue alert with audit events and no refund; a later loop did not duplicate them.

For a full mock HTTP replay on the seeded local database, run `.venv/bin/python scripts/demo_workflow.py`. It is safe to rerun with the same idempotency key; a second run must not add another ledger entry.

An approval can become stale if relevant order facts change. Calling the proposal endpoint again after such a change marks the old proposal stale and creates a new one requiring a new approval. All amounts are integer CNY cents calculated from the recorded paid allocation. The system does not automatically decide damaged goods, delivery disputes, complex payment splits, or exceptions to the demonstrated return policy.

On PostgreSQL, the proposal endpoint also saves a [LangGraph supervisor wait checkpoint](docs/implementation/approval-interrupt-2026-10-03.md). The authenticated decision endpoint resumes it after the SQL decision commits; stale replacement proposals close the old wait. The graph rereads SQL facts and cannot authorize or issue money from a resume payload. SQLite mock mode keeps the same SQL approval and refund rules without a durable approval checkpoint.

The same report documents an API process-restart replay and a fresh-database failure-injection check showing that an endpoint retry repairs a checkpoint failure after the SQL proposal or decision committed. The controlled worker still issued one ledger row after recovery.

## Verification

```bash
.venv/bin/python scripts/verify_minimum.py
.venv/bin/pytest -q
.venv/bin/python evals/runners/run_smoke.py
.venv/bin/python evals/runners/run_business.py
.venv/bin/python evals/runners/run_core_business.py
cd apps/web && npm run build
```

`verify_minimum.py` runs the seven no-key development checks together and writes an ignored aggregate manifest, suite log, JSONL, summary, and HTML report. A passing development minimum does not mark the independently reviewed locked release gate as passed. See the [minimum run report](docs/implementation/minimum-no-key-2026-10-04.md).

The smoke runner validates 30 fixed JSONL cases and runs three collaboration cases in both modes, each against a fresh SQLite database through the mock role-checked HTTP API. It writes `manifest.json`, `case_results.jsonl`, `summary.json`, and `report.html` under ignored `evals/reports/<run_id>/`. `summary.json` is the local machine-readable gate. The [database-first scorer](docs/implementation/scorer-mutation-2026-10-03.md) checks final return/refund facts, approval evidence, ownership, and citations; adversarial tests verify it rejects false outcomes. These cases are synthetic development cases, not a human-reviewed locked benchmark or a measured real-model comparison. The [stateful refund tests](docs/implementation/refund-stateful-2026-10-03.md) add generated action ordering and stale approved-fact checks. See the [30-case expansion report](docs/implementation/smoke-expansion-2026-10-03.md).

For a separate 30-utterance routing development set, run `.venv/bin/python evals/runners/run_intent_routing.py --mode mock` or `--mode live` with the ignored OpenRouter key configured. Both modes write manifest, JSONL, summary, and HTML under ignored `evals/reports/`; the live mode records provider token usage and cost. See the [intent routing report](docs/implementation/intent-routing-2026-10-03.md). These author-written labels are not independently reviewed gold.

Run `.venv/bin/python evals/runners/run_intent_dialogue.py` for 12 additional isolated multi-turn HTTP development cases covering missing return slots, explicit confirmation, order and package choice, changed order, clarification limits, handoff, and a read-only refund inquiry. The scorer checks response and database state after each turn. See the [dialogue report](docs/implementation/intent-dialogue-2026-10-04.md); these labels also await independent review.

To prepare independent review of the v6 minimum suites, run `.venv/bin/python scripts/prepare_minimum_review.py`. It writes an ignored local packet with dataset hashes, normalized synthetic cases and gold, two independent reviewer sheets, and an adjudication sheet. See the [review packet report](docs/implementation/minimum-review-packet-2026-10-04.md). Current cases remain development data; no locked split is generated by this command.

For an optional advisory OpenRouter review, keep the human `reviewer_a.csv` independent and run `.venv/bin/python scripts/run_model_review.py evals/review_packets/<packet-id> --limit 3` for a cost calibration, then rerun without `--limit` to resume. The runner uses the committed `review-v1` Prompt release, [review criteria v2](docs/implementation/eval-review-criteria-v2.md), and `openai/gpt-6-astra-pro` by default; `--max-cost-usd 30` caps cumulative recorded spend before the next call. It writes individual validated JSON results, `model_review.csv`, `human_model_disagreements.csv`, and a manifest under the ignored packet. It reads `OPENROUTER_API_KEY` from the environment or ignored `.env`; no credentials are written to the report. Re-run after filling `reviewer_a.csv` to refresh disagreements. The model is an additional reviewer, not one of the two humans required by v6, and its output does not change gold or create locked cases.

For the published policy retriever, run `.venv/bin/python evals/runners/run_policy_runtime.py --dialect sqlite` for the no-key path. Set `POLICY_RUNTIME_PG_ADMIN_URL` to an isolated Docker PostgreSQL `/postgres` connection and use `--dialect postgres` for a fresh migrated, version-scoped index. Both modes score 20 relevant and 20 near-negative author-written questions; see the [runtime retrieval report](docs/implementation/policy-runtime-2026-10-03.md). After upgrading code that changes `INDEX_VERSION`, refresh a deployed index with `docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/index_policies.py` before using policy answers.

For 20 paired composite development cases, run `.venv/bin/python evals/runners/run_collaboration.py --mode mock`; use `--mode live` with the ignored OpenRouter key to measure the configured GPT-4o-mini model. Each mode runs both agent paths against isolated synthetic fixtures and writes a local manifest, case and pair JSONL, summary, and HTML. The [collaboration report](docs/implementation/collaboration-development-2026-10-04.md) records the measured results and earlier failures. The labels are author-written development checks, not locked reviewed gold.
Paired runners use one recorded fixture seed timestamp across both paths in a run; the application wall clock continues normally.

Use `--suite single_domain` with either mode to check 20 paired order-only or policy-only questions without over-dispatch. The [single-domain report](docs/implementation/collaboration-single-domain-2026-10-04.md) records the mock and GPT-4o-mini results.

Run `.venv/bin/python evals/runners/run_collaboration_faults.py` for twelve paired test-only specialist error, incomplete, conflict, forged-evidence, and late-result injections. It uses isolated mock-auth HTTP replays and no provider key. The [fault report](docs/implementation/collaboration-faults-2026-10-04.md) records the results and the public-finding safety fix.

The original [business workflow report](docs/implementation/business-eval-2026-10-03.md) records four cross-role HTTP and controlled-worker cases on isolated SQLite databases with terminal-state gold. The current runner uses the six-case v2 dataset. Run it with `AUTH_MODE=mock`; these synthetic cases are a development gate while the human-reviewed locked suite remains open.

The [core business development suite](docs/implementation/core-business-minimum-2026-10-04.md) runs 30 customer Agent-to-terminal workflows across isolated SQLite databases, then scores the final database/audit state. Three cases check two Agent turns and the intermediate no-return state. Its `development_pass` and raw `v6_minimum_cases_met` are true; `release_gate_pass` stays false until independent review and a grouped locked split exist. The [first slice report](docs/implementation/core-business-slice-2026-10-04.md) preserves the initial implementation and idempotency fix.

The [v2 business suite and mutation measurement](docs/implementation/business-mutation-2026-10-03.md) add explicit denial cases for missing confirmation and expired return windows. The v2 runner passed six cases; `AUTH_MODE=mock .venv/bin/python evals/runners/run_business_mutations.py` detected nine named application faults against those cases. The v1 JSONL remains as a historical development fixture.

For the extended [refund rule-state property campaign](docs/implementation/rule-state-2026-10-03.md), run `STATEFUL_REFUND_EXAMPLES=2000 .venv/bin/pytest -q tests/test_refund_stateful.py::test_random_refund_action_sequences_preserve_money_and_approval --hypothesis-show-statistics`. The normal full suite uses 100 generated sequences so routine development stays fast.

The same six cases also passed in the [real Keycloak OIDC and fresh PostgreSQL runner](docs/implementation/business-oidc-postgres-2026-10-03.md). Set `BUSINESS_PG_ADMIN_URL` to a separate PostgreSQL server's `/postgres` database and run `.venv/bin/python evals/runners/run_business_oidc_postgres.py`. The runner creates a new migrated synthetic database per case, starts a temporary API, and invokes a separate worker process; its report is ignored by Git. Keep this evaluation away from the live demo database.

Add `--core` to replay the 30 conversational core-business development cases with real Keycloak tokens and a fresh PostgreSQL database per case. Its local `development_pass` and raw case-count flag can be true while `gate_pass` and `release_gate_pass` remain false because independent review and locked testing are still open. See the [30-case report](docs/implementation/core-business-minimum-2026-10-04.md).

For process-kill recovery, run the same command with `--case-id business-approved-refund --kill-at-checkpoint proposal` or `--kill-at-checkpoint decision`. A test-only Uvicorn wrapper sends SIGKILL immediately after the first checkpoint database write, then a new API retries the committed action. The [checkpoint kill report](docs/implementation/checkpoint-kill-2026-10-03.md) records both PostgreSQL outcomes. The production API has no crash switch.

To replay the approved refund while Langfuse is unreachable, add `--case-id business-approved-refund --langfuse-outage`. This uses fake project keys and a closed local port, checks the committed PostgreSQL ledger and audit, and keeps the API export failure in the ignored report log. See the [outage check](docs/implementation/cloud-outage-2026-10-03.md).

An optional [11-case paired GPT-4o-mini development run](docs/implementation/paired-model-expanded-2026-10-03.md) uses `.venv/bin/python evals/runners/run_paired_model.py --scope development --seed 20261003` after the ignored local OpenRouter key is configured. It compares three composite and eight single-domain synthetic cases with recorded execution order, provider usage, and the same database-first scorer. It is not a human-reviewed locked evaluation.

The [40-story memory A/B](docs/implementation/memory-ab-2026-10-03.md) compares PostgresStore and Mem0 OSS in a separate PostgreSQL database. PostgresStore passed 40/40; Mem0 retained old corrected preferences and withdrawn utterances in its local history, so it is offline only. Compose enables the guarded PostgresStore route with `LONG_TERM_MEMORY_MODE=postgres_store`, while the SQLite mock setup leaves it `off`. Profile writes require consent and explicit confirmation; reads are scoped to the authenticated customer and fail closed if the store disagrees with the structured SQL profile. To reproduce the isolated A/B and seven application checks, follow the commands in the memory report. Do not run that evaluator against the live demo database.

Confirmed `language=English` (also `en` or `en-US`) changes parent chat responses to English; correction, deletion, or consent withdrawal takes effect on the next request. This affects wording only. The [real-OIDC verification](docs/implementation/parent-memory-answer-2026-10-03.md) restores the synthetic demo profile after checking language use and customer isolation.

Preference deletion and consent withdrawal also [erase the revoked value](docs/implementation/memory-erasure-2026-10-03.md) from the current SQL row and PostgresStore; the row identity and audit remain. A migration scrubs previously revoked SQL values. This does not erase historical backups or exports.

The customer page includes consent and language preference controls. The [OIDC browser check](docs/implementation/memory-browser-2026-10-03.md) covers save, next-chat use, deletion, and restoration of the original synthetic profile.

The optional [PostgreSQL memory write-load runner](docs/implementation/memory-write-load-2026-10-03.md) creates a fresh isolated database and measures 1/5/10 concurrent synthetic customer preference corrections and guarded reads. Its report records actual latency and final revocation checks; it leaves the database for inspection.

When an OpenRouter key is already configured, `.venv/bin/python evals/runners/run_paired_model.py` runs the three synthetic composite smoke cases in single and collaborative modes using the configured GPT-4o-mini model. The [measured development report](docs/implementation/paired-model-2026-10-03.md) gives provider-reported usage and cost; it is a small comparison rather than a locked quality benchmark.

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

The browser suite runs with `cd apps/web && npm run test:e2e` after installing Playwright Chromium, or with `CHROME_TEST_PATH` pointing at an existing Chrome binary. It covers customer, support, warehouse, and supervisor sign-in. The test reads only the ignored local account file. The three-role [return-to-refund browser workflow](docs/implementation/browser-workflow-2026-10-02.md) and [exception/stale-proposal journeys](docs/implementation/browser-exceptions-2026-10-03.md) require a fresh isolated OIDC API and `BROWSER_FLOW_API_URL`. The [multi-package browser journey](docs/implementation/package-selection-2026-10-03.md) also needs an MCP service on that isolated database and `BROWSER_PACKAGES_API_URL`. The default suite skips these fixture journeys so the live demo database is never changed by routine browser checks.

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

Health probes are excluded from Cloud export. Cloud masking removes named sensitive attributes and prompt/completion content while retaining numeric usage counts. Local OTLP export uses `OTEL_EXPORTER_OTLP_ENDPOINT`; the Collector separately removes named sensitive attributes before Jaeger. A [live integration check](docs/implementation/langfuse-integration-2026-10-02.md) verified both exporters, a masking canary, prompt links, generation usage/cost, and score ingestion. Raw chat text is not included in the generation spans. To reconcile an evaluation run after Cloud ingestion, use `scripts/export_langfuse_run.py <run_id>`; one [28-execution synthetic smoke run](docs/implementation/langfuse-eval-reconciliation-2026-10-02.md) reconciled all traces, case links, and scores.

Run `.venv/bin/python scripts/sync_langfuse_dashboard.py` to inspect the versioned custom dashboard, or add `--apply` to create missing widgets and placements in the configured project. The sync detects drift and never overwrites Cloud edits. Run `.venv/bin/python scripts/measure_langfuse_usage.py --month YYYY-MM --budget 50000` for aggregate observation/score counts and a unit proxy using the assumed Hobby budget. The authoritative billable total and actual project plan are in Langfuse **Dashboards → Langfuse Usage Management**. See the [dashboard and usage check](docs/implementation/langfuse-dashboard-2026-10-03.md). Larger locked-run reconciliation remains open.

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
