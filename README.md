# ResolveAI

ResolveAI is a synthetic, single-company e-commerce after-sales demonstration. It offers a no-key local mock path for customer order lookup, read-only policy and logistics specialists, confirmed return requests, warehouse inspection, supervisor approval, and one idempotent simulated refund. It does not connect to a real payment provider. PostgreSQL with pgvector is the v6 primary database; SQLite is the lightweight fallback for local mock tests. The [v6 specification](plans/resolveai-v6.md) describes the full target; [verified status](docs/STATUS.md) distinguishes implemented work from remaining integration work.

The first runnable development preview is [v0.1.0](docs/releases/v0.1.0.md). It does not pass the locked release gate.

## Docker Compose run (recommended)

From the repository root, start the local mock stack with PostgreSQL 17, pgvector, API, web UI, Redis/Celery worker and scheduler, Commerce MCP, OTel Collector, and Jaeger. No API keys are needed:

```bash
docker compose -f infra/compose/compose.yaml up --build -d
docker compose -f infra/compose/compose.yaml ps
docker compose -f infra/compose/compose.yaml exec -T api python scripts/verify_postgres.py --expected-orders 25
```

The API runs Alembic migrations and seeds 25 synthetic orders on first start. Repeated starts keep the existing PostgreSQL volume and demo data. Open `http://localhost:3000` for the mock role workflow (`cust-01` as `customer`, then `warehouse-01` as `warehouse` and `supervisor-01` as `supervisor`). API docs are at `http://localhost:8000/docs`; Jaeger is at `http://localhost:16686`. The mock identity controls are for local loopback use only. The Commerce MCP container starts but requires Keycloak-issued JWTs, so its tools are unavailable in the default mock mode.

To replay the synthetic approval-to-refund workflow, inspect logs, or stop the stack while retaining its data:

```bash
docker compose -f infra/compose/compose.yaml exec -T api python scripts/demo_workflow.py
docker compose -f infra/compose/compose.yaml logs --tail=100 api worker beat
docker compose -f infra/compose/compose.yaml down
```

The [Docker integration report](docs/implementation/docker-compose-2026-09-30.md) records the actual PostgreSQL 17, chat, refund, restart, and Jaeger checks. A separate [PostgreSQL 16 report](docs/implementation/postgres-integration-2026-09-30.md) covers the 100,000-order import. Set `DATABASE_URL` to a PostgreSQL URL to run API and worker against another local PostgreSQL installation.

For overlapping refund worker and pre-commit rollback recovery checks, use a fresh isolated PostgreSQL database and the commands in the [worker recovery report](docs/implementation/refund-worker-recovery-2026-10-03.md).

An isolated [worker container restart drill](docs/implementation/worker-container-restart-2026-10-03.md) also force-killed the worker after one committed refund; a new container issued nothing further and the database retained one correct ledger and audit event.

An isolated million-order PostgreSQL import and 20/50/100-user no-key k6 read/chat load were measured without changing the live demo database. The [scale report](docs/implementation/million-order-load-2026-10-02.md) has the commands, actual P95s, error rates, resource snapshots, and limits; the load script is [million_orders.js](evals/load/million_orders.js).

For sustained HTTP write traffic, run `.venv/bin/python evals/runners/run_mixed_write_load.py` with Docker and cached PostgreSQL/k6 images. It creates its own synthetic database, API and worker, then measures ten concurrent users for 60 seconds through return, warehouse, approval and simulated refund. The [write-load report](docs/implementation/mixed-write-load-2026-10-05.md) records 697 completed workflows, zero HTTP errors, terminal safety checks and the approval/worker race it exposed and fixed.

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

Open `http://localhost:3000`. The page exposes customer, support, warehouse, and supervisor workflows. Policy publication is available in the API docs. In mock mode the UI actor and role inputs are development controls, not real authentication.

## Business workflow

1. Customer: `GET /orders`, `GET /orders/{id}/shipments`, and `POST /chat` with a stable `thread_id`. Use `agent_mode=single` or `collab` to compare read-only evidence routing. The response includes `trace_id` and structured specialist findings.
   For an order with multiple packages, chat asks the customer to select a `shipment_id` before giving logistics facts. The web UI provides a package selector; the [package selection report](docs/implementation/package-selection-2026-10-03.md) records the isolated OIDC/MCP browser check and the current split-delivery return limit.
2. Customer: `POST /returns` with an owned order/item, positive quantity, reason, `confirmed=true`, and an idempotency key. The service recalculates eligibility using the Shanghai business calendar.
3. Warehouse: `POST /warehouse/returns/{id}/receipt`, then `/inspection`, then `POST /returns/{id}/proposal`.
4. Supervisor: `GET /supervisor/proposals`, then `POST /supervisor/proposals/{id}/decision`.
5. Controlled worker: run `.venv/bin/python -m resolveai.worker` for the direct local setup. Only approved, current proposals are issued; retries use `refund:{proposal_id}` and create at most one ledger entry. The same one-shot worker records one warning in the final 24 hours of the seven-day period after receipt and one overdue alert. Supervisors can view them at `GET /supervisor/refund-deadlines`. Compose runs private Redis/Celery refund and deadline jobs every 30 seconds through one Beat scheduler and a two-child worker. [Queue/recovery verification](docs/implementation/celery-jobs-2026-10-05.md) checked duplicates, isolation, worker restart and broker outage; SQL approval remains independent of Redis. The customer and Agent APIs have no refund issuance endpoint.

A human handoff returns a `ticket_id`. The customer can read and message their own ticket through `GET /tickets`, `GET /tickets/{ticket_id}`, and `POST /tickets/{ticket_id}/messages`. A supervisor assigns an open ticket through `POST /supervisor/tickets/{ticket_id}/assign`; only that support actor can reply or close it with `POST /tickets/{ticket_id}/resolve` and a final message. These actions are also available in the web UI. [Ticket workflow verification](docs/implementation/ticket-handoff-2026-10-06.md) covers ownership and audit. Resolving a ticket has no refund authority or effect on a return decision.

The ticket panel can show all, open, or resolved tickets. `GET /tickets?status=open` filters the owned or assigned queue. For stable older pages, pass the last row's `created_at` and `id` as `before_created_at` and `before_id`; the web UI does this for **加载更多工单**. The [queue filter report](docs/implementation/ticket-queue-filter-2026-10-06.md) records verification and the current deployment block.

A failed warehouse inspection also creates one [linked human ticket](docs/implementation/inspection-exception-ticket-2026-10-06.md) automatically. Refresh **My tickets** to see its return ID and the warehouse note; **加载更多工单** pages through older work. A supervisor still assigns support; resolving the ticket does not turn the inspection into a pass or permit a refund. Existing failed inspections are linked during database migration. The API uses `GET /tickets?offset=0` for the newest 100 and increments the offset for older pages.

When the automatic return rules deny an owned order, a confirmed customer can use **自动退货不适用时申请人工复核** or `POST /returns/review` with the order, item, count, reason, and a separate idempotency key. Policy exceptions, unverified delivery, and outside-window requests create an [audited human ticket](docs/implementation/return-eligibility-review-2026-10-06.md); incorrect or overcommitted quantities must be corrected. `POST /returns` keeps its existing eligibility error. Support resolution communicates an outcome but cannot create a return or refund.

For a shortage or other quantity mismatch before receipt, warehouse staff can enter the observed count and an explanation, then choose **上报数量异常并转人工**. The same action is `POST /warehouse/returns/{id}/receipt-dispute` with `observed_quantity` and `note`. It creates one linked ticket and places the return in `exception`. An exact retry returns that ticket; closing the support ticket does not permit receipt or refund. See the [receipt discrepancy report](docs/implementation/receipt-dispute-handoff-2026-10-06.md). Manual adjudication of the disputed return remains a separate workflow.

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

For an optional advisory OpenRouter review, keep the human `reviewer_a.csv` independent and run `.venv/bin/python scripts/run_model_review.py evals/review_packets/<packet-id> --limit 4` for a cost calibration, then rerun without `--limit` to resume. The runner uses the committed `review-v1` Prompt release, [review criteria v3](docs/implementation/eval-review-criteria-v3.md), and pinned `openai/gpt-6-luna` by default, with a $1 packet spend stop checked before each call. `--model openai/gpt-6-sol` selects the stronger, more costly targeted reviewer; `--model deepseek/deepseek-v3.2` and `--model openai/gpt-6-astra-pro` preserve historical streams. Each model writes to a separate ignored directory. The runner stores individual validated JSON results, `model_review.csv`, `human_model_disagreements.csv`, and a manifest; it reads `OPENROUTER_API_KEY` from the environment or ignored `.env` without writing credentials to the report. A single invalid structured answer is retried once, with both calls counted against the spend guard. After filling `reviewer_a.csv`, run the same model command with `--refresh-only` to update disagreements without a key or model call. The [Luna review report](docs/implementation/model-review-luna-2026-10-04.md) records the 162-case pass and its errors; the [Sol report](docs/implementation/model-review-sol-2026-10-04.md) records the targeted check of Luna's 62 non-accept cases. The model is an additional reviewer, not one of the two humans required by v6, and its output does not change gold or create locked cases.

For the published policy retriever, run `.venv/bin/python evals/runners/run_policy_runtime.py --dialect sqlite` for the no-key path. Set `POLICY_RUNTIME_PG_ADMIN_URL` to an isolated Docker PostgreSQL `/postgres` connection and use `--dialect postgres` for a fresh migrated, version-scoped index. Both modes score 20 relevant and 20 near-negative author-written questions; see the [runtime retrieval report](docs/implementation/policy-runtime-2026-10-03.md). After upgrading code that changes `INDEX_VERSION`, refresh a deployed index with `docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/index_policies.py` before using policy answers.

For 20 paired composite development cases, run `.venv/bin/python evals/runners/run_collaboration.py --mode mock`; use `--mode live` with the ignored OpenRouter key to measure the configured GPT-4o-mini model. Each mode runs both agent paths against isolated synthetic fixtures and writes a local manifest, case and pair JSONL, summary, and HTML. The [collaboration report](docs/implementation/collaboration-development-2026-10-04.md) records the measured results and earlier failures. The labels are author-written development checks, not locked reviewed gold.
Paired runners use one recorded fixture seed timestamp across both paths in a run; the application wall clock continues normally.

Use `--suite single_domain` with either mode to check 20 paired order-only or policy-only questions without over-dispatch. The [single-domain report](docs/implementation/collaboration-single-domain-2026-10-04.md) records the mock and GPT-4o-mini results.

Run `.venv/bin/python evals/runners/run_collaboration_faults.py` for twelve paired test-only specialist error, incomplete, conflict, forged-evidence, and late-result injections. It uses isolated mock-auth HTTP replays and no provider key. The [fault report](docs/implementation/collaboration-faults-2026-10-04.md) records the results and the public-finding safety fix.

For actual external transport faults, set `SPECIALIST_TRANSPORT_PG_ADMIN_URL` to an isolated PostgreSQL `/postgres` connection and run `.venv/bin/python scripts/verify_specialist_transport.py`. It uses real local Keycloak accounts, fresh migrated databases, authenticated Commerce MCP processes, and a local model stub: no provider credit is spent. Both modes check trickling responses, slow tools with late completion, contradictory shipment evidence, held PostgreSQL order locks, and fresh-turn recovery. See the [resource and transport report](docs/implementation/request-resources-2026-10-04.md).

Chat turns share resource limits across intent, both specialists, retries, and Replan: `AGENT_REQUEST_TIMEOUT_SECONDS=25`, `AGENT_MAX_LLM_CALLS=10` (hard maximum ten), `AGENT_MAX_TOKENS=16000`, and `AGENT_MAX_COST_USD=0.02`. Configure these in the API environment or Compose `.env`. Responses include `resource_usage`; invalid limits reject startup. Token/cost reservations are conservative admission estimates, and unknown usage remains accounted. Provider billing can differ. PostgreSQL chat statements use remaining-time deadlines; a timed-out transaction rolls back before a separate bounded handoff. Pool admission, guarded connection establishment and parent checkpoint socket/lock waits also respect the active budget; timed-out PostgreSQL transactions close locally before fresh ownership-checked handoff. SQLite retains local rollback. A stuck resolver may occupy one of four connection slots until recovery/restart; late connections close. See the [I/O deadline report](docs/implementation/database-io-deadlines-2026-10-05.md). See the [SQL deadline report](docs/implementation/sql-request-deadline-2026-10-04.md). Commerce MCP uses stateless JSON responses with JWT and per-object authorization.

The original [business workflow report](docs/implementation/business-eval-2026-10-03.md) records four cross-role HTTP and controlled-worker cases on isolated SQLite databases with terminal-state gold. The current runner uses the six-case v2 dataset. Run it with `AUTH_MODE=mock`; these synthetic cases are a development gate while the human-reviewed locked suite remains open.

The [core business development suite](docs/implementation/core-business-minimum-2026-10-04.md) runs 30 customer Agent-to-terminal workflows across isolated SQLite databases, then scores the final database/audit state. Three cases check two Agent turns and the intermediate no-return state. Its `development_pass` and raw `v6_minimum_cases_met` are true; `release_gate_pass` stays false until independent review and a grouped locked split exist. The [first slice report](docs/implementation/core-business-slice-2026-10-04.md) preserves the initial implementation and idempotency fix.

The [v2 business suite and mutation measurement](docs/implementation/business-mutation-2026-10-03.md) add explicit denial cases for missing confirmation and expired return windows. The v2 runner passed six cases; `AUTH_MODE=mock .venv/bin/python evals/runners/run_business_mutations.py` detected nine named application faults against those cases. The v1 JSONL remains as a historical development fixture.

For the extended [refund rule-state property campaign](docs/implementation/rule-state-2026-10-03.md), run `STATEFUL_REFUND_EXAMPLES=2000 .venv/bin/pytest -q tests/test_refund_stateful.py::test_random_refund_action_sequences_preserve_money_and_approval --hypothesis-show-statistics`. The normal full suite uses 100 generated sequences so routine development stays fast.

The same six cases also passed in the [real Keycloak OIDC and fresh PostgreSQL runner](docs/implementation/business-oidc-postgres-2026-10-03.md). Set `BUSINESS_PG_ADMIN_URL` to a separate PostgreSQL server's `/postgres` database and run `.venv/bin/python evals/runners/run_business_oidc_postgres.py`. The runner creates a new migrated synthetic database per case, starts a temporary API, and invokes a separate worker process; its report is ignored by Git. Keep this evaluation away from the live demo database.

Add `--core` to replay the 30 conversational core-business development cases with real Keycloak tokens and a fresh PostgreSQL database per case. Its local `development_pass` and raw case-count flag can be true while `gate_pass` and `release_gate_pass` remain false because independent review and locked testing are still open. See the [30-case report](docs/implementation/core-business-minimum-2026-10-04.md).

For process-kill recovery, run the same command with `--case-id business-approved-refund --kill-at-checkpoint proposal` or `--kill-at-checkpoint decision`. A test-only Uvicorn wrapper sends SIGKILL immediately after the first checkpoint database write, then a new API retries the committed action. The [checkpoint kill report](docs/implementation/checkpoint-kill-2026-10-03.md) records both PostgreSQL outcomes. The production API has no crash switch.

For an actual client process kill inside an uncommitted PostgreSQL transaction, set `TRANSACTION_KILL_PG_ADMIN_URL` to an isolated PostgreSQL `/postgres` connection and run `.venv/bin/python scripts/verify_transaction_kill_postgres.py`. It creates two new synthetic databases, observes the flushed transactions, kills only its disposable clients, and checks rollback plus idempotent recovery in fresh processes. Reports and process logs remain under ignored `evals/reports/`; databases remain for inspection. See the [transaction kill report](docs/implementation/transaction-kill-2026-10-04.md).

For actual PostgreSQL **server** SIGKILL and WAL recovery, run `.venv/bin/python scripts/verify_postgres_server_crash.py` with Docker and the cached `pgvector/pgvector:pg17` image available. It creates its own labeled container and volume, validates ownership before destructive operations, and checks rollback plus exactly-once recovery for two uncommitted business actions. Successful resources are removed; failed resources and safe diagnostics remain for inspection. See the [server crash report](docs/implementation/postgres-server-crash-2026-10-05.md).

To verify a **local PostgreSQL logical backup and restore**, run `.venv/bin/python scripts/verify_postgres_backup_restore.py` with the same cached image. It builds two synthetic business states and checkpoints, stores private ignored archives, restores each into a distinct owned server, compares database contents and schema, and repeats the domain action to check idempotency. The [restore report](docs/implementation/postgres-backup-restore-2026-10-05.md) records the result and the separate offline-copy work still open.

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

To reproduce the mock [actor-switch race checks](docs/implementation/browser-actor-switch-2026-10-06.md), start a separate `NEXT_PUBLIC_AUTH_MODE=mock NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev -- -p 3001 -H 127.0.0.1` from `apps/web`, then run `ACTOR_SWITCH_TEST=1 PROFILE_RACE_TEST=1 npx playwright test tests/actor-switch.spec.ts tests/profile-switch.spec.ts` there. These tests intercept synthetic API responses; no backend or provider key is needed.

The local [Prompt catalog](prompts/catalog/) is the only editable Prompt source. [release-v1](prompts/releases/release-v1.json) preserves the original hashes; the Docker API now defaults to [specialists-dev-v1](prompts/releases/specialists-dev-v1.json), a committed development artifact that enables bounded model tool selection and policy-query planning. `PROMPT_RELEASE=release-v1` selects the original planning baseline. `PromptRegistry` refuses a hash mismatch at startup. `scripts/release_prompts.py` makes a manifest only after a catalog commit and records development verification with the locked release gate false. Cloud Prompt text is never loaded for inference. The mock path does not need Langfuse keys.

For local Python development, set `PROMPT_RELEASE=specialists-dev-v1` before starting the API or runners. Order plans select only `get_order`/`track_shipment`; the server binds the authenticated reference. Policy plans issue at most two queries inside the server-selected bundle. A status-only question can use one order read, while insufficient shipping evidence is explicitly withheld. Both planning calls use the shared resource account. Technical planner failures use safe deterministic read plans; valid empty plans preserve the evidence gap. See the [planning report](docs/implementation/specialist-tool-planning-2026-10-05.md).

```bash
PROMPT_RELEASE=specialists-dev-v1 .venv/bin/python scripts/verify_minimum.py
PROMPT_RELEASE=specialists-dev-v1 .venv/bin/python evals/runners/run_paired_model.py --scope development --case-id smoke-11 --case-id smoke-20 --case-id smoke-22
```

The second command uses the ignored OpenRouter key and paid GPT-4o-mini calls; the recorded six-case replay cost $0.000819. The user approved the [specialist Langfuse mirror](docs/implementation/specialist-prompt-mirror-2026-10-05.md): all nine templates synchronized and read back with zero drift. Matching new generations can now attach Cloud prompt versions through the ignored mirror map. Local committed prompt text remains authoritative.

The parent graph [merges duplicate findings by task/revision](docs/implementation/duplicate-dispatch-2026-10-05.md) and shares one read execution for repeated dispatches. `.venv/bin/python scripts/verify_specialist_transport.py --scenario duplicate_dispatch` uses the same real-Keycloak/PostgreSQL setup as the other transport probes and a free local model stub; omitting `--scenario` now runs ten cases.

Set `OPENROUTER_API_KEY` to enable structured intent extraction and bounded specialist evidence review using the exact model ID in [the model registry](packages/agent/models-mock-v1.json). For Docker Compose, put `OPENROUTER_API_KEY=...` in a repository-root `.env` file (ignored by Git) with mode `600` and start with `docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api`. Do not paste the key into chat or command arguments. The deterministic mock remains the no-key path. One [live structured-intent request](docs/implementation/openrouter-integration-2026-10-02.md) and a separate two-specialist evidence review passed on GPT-4o-mini; broader model quality and cost benchmarks remain open.

### Langfuse Cloud

Add `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL=https://us.cloud.langfuse.com` to the same local `.env` for the configured US project. The project keys identify the project; no project name is needed in the application. Keep the existing OpenRouter key. Then run:

```bash
docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api worker beat
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/sync_prompts_to_langfuse.py release-v1
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/sync_prompts_to_langfuse.py release-v1 --check
```

Open the project in Langfuse: **Prompts** contains six mirrored templates and **Tracing** contains subsequent chat traces. Generations show the configured model, provider-reported token usage/cost, and the mirrored prompt version. Inference still reads the hash-verified local catalog. Rerun sync when publishing a new local prompt release. The Compose `observability` volume preserves the version mapping across API container recreation; removing that volume requires another sync.

Cloud export keeps the `/chat` or `/chat/stream` request root and the two FastAPI spans needed for its hierarchy, model generations, specialist/tool work, and explicit evaluation request roots. Routine order/return API traffic, health probes, and worker jobs stay out of Langfuse. For a deliberate evaluation that needs Cloud traces on non-chat endpoints, set `LANGFUSE_EXPORT_EVAL_TRACES=1` on the API and send validated `X-Eval-Run-Id` and `X-Eval-Case-Id` headers; leave it at the default `0` for normal use. Keep `LANGFUSE_SAMPLE_RATE=1` for a run whose Cloud trace/score reconciliation must be complete. Cloud masking removes named sensitive attributes, URL and request metadata, and prompt/completion content while retaining numeric usage counts. Local OTLP export uses `OTEL_EXPORTER_OTLP_ENDPOINT`; the Collector separately removes named sensitive attributes before Jaeger. A [live integration check](docs/implementation/langfuse-integration-2026-10-02.md) verified the earlier wider export, a masking canary, prompt links, generation usage/cost, and score ingestion; the current narrower export passed a [fresh two-trace Cloud reconciliation](docs/implementation/langfuse-essential-reconciliation-2026-10-06.md). Raw chat text is not included in the generation spans. To reconcile an evaluation run after Cloud ingestion, use `scripts/export_langfuse_run.py <run_id>`; one [28-execution synthetic smoke run](docs/implementation/langfuse-eval-reconciliation-2026-10-02.md) reconciled all traces, case links, and scores before this export change.

For a small, no-key verification of the current Cloud filter, run `.venv/bin/python scripts/verify_langfuse_essential.py`. It reads only Langfuse settings from ignored `.env`, clears the OpenRouter key in its process, runs one synthetic development case in both Agent modes, and polls for trace/score reconciliation. Its local report has `targeted_pass=true` when the selected case passes and always leaves the full `gate_pass=false`. The [essential reconciliation report](docs/implementation/langfuse-essential-reconciliation-2026-10-06.md) records the actual US Cloud check and its limits.

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

## Controlled asynchronous jobs

Compose starts Redis, `worker` and one `beat` scheduler by default. `JOB_NAMESPACE=dev` selects the job namespace; database identity also isolates queues and Redis keys. The direct local one-shot worker needs no broker. For local Celery processes, configure `CELERY_BROKER_URL` and run `.venv/bin/celery -A resolveai.jobs:app worker --concurrency=2` and one `.venv/bin/celery -A resolveai.jobs:app beat --schedule=.local/celery-beat`. Keep worker and scheduler on the same database/namespace. PostgreSQL job URLs must include host, port and database; implicit libpq targets and service overrides reject startup.

Run `.venv/bin/python scripts/verify_celery_jobs.py` with Docker and cached Redis/PostgreSQL images for the isolated synthetic queued-job, restart and outage probe. The [job report](docs/implementation/celery-jobs-2026-10-05.md) records task limits, terminal SQL checks and remaining bulk-job work. There is no public refund issuance or arbitrary job-dispatch endpoint.

Queued deadline scans select only pending alerts and process at most100 candidates per job. [Batch verification](docs/implementation/deadline-job-batches-2026-10-05.md) checked205 overdue receipts progressing100/100/5/0 without rescanning completed rows. Direct one-shot calls retain unlimited pending processing, with an optional validated batch size.

Verify pool/connection/checkpoint deadlines without model credit using `.venv/bin/python scripts/verify_database_io_deadlines.py`. It creates only an owned disposable synthetic PostgreSQL server, removes successful resources and retains stopped failed fixtures plus ignored reports.

Replay the twelve existing multi-turn development cases through real Keycloak, private MCP/API processes and fresh per-case PostgreSQL databases with `.venv/bin/python scripts/verify_intent_dialogue_oidc.py`. The [measured dialogue report](docs/implementation/intent-dialogue-oidc-2026-10-05.md) includes API restarts, per-turn SQL counts and customer isolation; inference and Cloud calls are disabled. Independent human gold and locked evaluation remain open.
