# Implementation and verification log

Updated 2026-10-02. This log records encountered failures, fixes, and open integration gates. It is not a claim that the v6 plan is fully finished.

## Resolved during implementation

| Symptom | Resolution and evidence |
|---|---|
| System Python had no `pip` or `ensurepip` | Used `uv venv` and a writable `UV_CACHE_DIR` under `/tmp`; editable Python install succeeded. |
| `uv` default cache path was read-only | Re-ran with `UV_CACHE_DIR=/tmp/resolveai-uv-cache`. |
| First fixture seed failed with SQLite foreign-key error | Flushed parent customers, policy, orders, items, and shipments before dependent rows. Tests and 25-case smoke runner passed afterward. |
| No system Node/npm | Downloaded Node 22.23.3 to `/tmp`, compared archive SHA-256 with Node's published `SHASUMS256.txt`, and ran the web build. |
| `keycloak-js@26.3.3` was unavailable from npm | Checked published npm version; pinned `26.2.4`. `npm install` and `npm run build` then passed. |
| Local `.git` was initially read-only to the sandbox | Escalated `git init`, staging, and the catalog commit; generated `release-v1` against the actual catalog commit. |
| First smoke run had two routing failures | Complaint handoff and general signed-goods policy routing were fixed; subsequent 25-case run passed all 28 executions. |
| First lexical retrieval revision made a return question lack policy evidence | Normalized “我要退” to the return topic and made incomplete findings safe for the scorer; the subsequent smoke run passed. |
| A replayed return idempotency key could expose another customer's request ID | Scoped replay results to the authenticated customer, required confirmation on retries, and locked the owned order row before the quantity check on PostgreSQL. The regression test and 25-case smoke runner passed. |
| Backup restore proof was missing | Created `/tmp/resolveai-backup-20260929.bundle` from the committed repository, verified complete history, cloned it to `/tmp/resolveai-restore-20260929`, confirmed HEAD `cd94ed7`, and loaded `release-v1` successfully from the restored Prompt catalog. Bundle SHA-256: `e9acc987f302faa93ba5a3248a97894b5d36da3c296ac29cf1945e30af20ef90`. This is a local drill; no separate offline device was available. |
| Received returns had no deadline warning | Added a one-shot, idempotent 24-hour warning and overdue alert with an audit event, supervisor read endpoint, and supervisor UI list. The endpoint suppresses completed refunds and superseded due-soon alerts. The new migration upgraded on SQLite, and boundary/retry/role tests plus the web build passed. Compose invokes the worker every 30 seconds, but live delivery remains unverified. |
| The HTTP path lacked an automated cross-role refund check | Added an isolated TestClient workflow covering another customer's denial, idempotent return retry, warehouse receipt/inspection, approval-before-issue, supervisor role rejection, one ledger entry on worker retry, and audit actions. The test passed on SQLite. |
| No real PostgreSQL integration had been run | Downloaded PostgreSQL 16.15 and pgvector 0.6.0 packages into `/tmp`, initialized an unprivileged server, and ran clean migrations, a vector operation, the full synthetic refund flow and replay, 100k `COPY`, and read-only quality checks. See [PostgreSQL report](postgres-integration-2026-09-30.md). |
| PostgreSQL rejected the active-policy index migration | SQLite accepted `active IS 1`; PostgreSQL did not. Changed only the PostgreSQL predicate to `active IS TRUE` and reran migrations on an empty database. |
| PostgreSQL migrations did not enable pgvector | Added conditional Alembic revision `9d24757b98e1` that creates `vector` on PostgreSQL and leaves SQLite unchanged. A clean migration and a vector distance query passed. |
| First 100k import targeted an older partial generated directory | Retried against independently validated `realistic-100k-v3`; all 17 CSV tables imported in 6.508 seconds. |
| Restricted sandbox stalled FastAPI TestClient after the PostgreSQL changes | A fault-handler stack showed a blocked AnyIO portal; rerunning the unchanged 22-test suite and 25-case smoke runner outside the restricted sandbox passed. |
| First Docker chat request timed out | `PostgresSaver.setup()` attempted a concurrent index while the same request held a PostgreSQL transaction. Moved checkpoint setup to API startup; Docker chat then returned HTTP 200 in under a second. |
| PostgreSQL checkpoint replay included prior-turn findings | Scoped the graph checkpoint key to customer, thread, and plan revision; a MemorySaver regression test and Docker two-turn replay each returned two findings from the current revision only. |
| Docker API startup showed an OTel provider warning and metrics 404 exports | Reused an existing SDK provider, set Compose service names, and disabled unused metrics export. Rebuilt API/worker logs showed no recurrence; Jaeger received a seven-span chat trace under `resolveai-api`. |
| First live OpenRouter chat fell back to handoff | The key passed OpenRouter's `/api/v1/key` check, but the Pydantic schema lacked strict-mode `required` and `additionalProperties: false`. Normalized the schema before sending it; one live GPT-4o-mini synthetic order-status request then returned an answered response. See [OpenRouter report](openrouter-integration-2026-10-02.md). |
| Langfuse setup had incomplete generation metadata and filtering | Added generation spans with usage/cost and local prompt mirror links; retained FastAPI parents, excluded health probes, and preserved numeric usage when masking tokens. The six-span live chat had no unresolved parents, and both exporters removed the synthetic canary. Prompt sync now creates its output directory; a Docker volume persists the mapping. |

## Open gates needing external runtime, credentials, or further implementation

| Gate | Current evidence and next concrete action |
|---|---|
| Keycloak/MCP and deeper Docker operations | Docker Compose PostgreSQL 17, PostgresSaver chat, worker execution, API restart, and OTel→Jaeger trace passed. Keycloak sign-in and authenticated MCP calls still require configured users; refund worker concurrency, full container restart recovery, and live deadline delivery remain unverified. The Git bundle still needs copying to a separate offline medium. |
| Langfuse Cloud | US project credentials now authenticate. Six prompts synced and passed drift checks; a synthetic masking canary passed in Cloud and Jaeger, and a live GPT-4o-mini generation recorded usage/cost and its prompt version. A setup score was read back. Full evaluation export/reconciliation, custom dashboards, and quota calibration remain open. See [integration report](langfuse-integration-2026-10-02.md). |
| OpenRouter real-model path | A local ignored key and one live GPT-4o-mini structured-intent call now work in Docker. Provider/schema regression trials, cost measurements, model comparison, and paired locked evaluations remain open. |
| Locked evaluation and human review | The 25 smoke cases are original synthetic development fixtures. The planned 30/30/20/20 minimum and expanded suites, two-person critical review, mutation/property suite, paired model comparison, and external benchmarks remain to be built and reviewed. Do not treat 28/28 mock executions as a production success rate. |
| Policy retrieval | pgvector is installed and its distance operator works in PostgreSQL 16. The no-key retrieval still uses deterministic lexical/character-gram RRF. PostgreSQL full-text + pgvector policy indexes, embeddings, index versioning, and historical policy publication/rollback regression remain open. |
| Memory A/B | Confirmed preferences, consent, correction, deletion, and ownership are tested. PostgresStore and Mem0 OSS A/B with 40 multi-session stories and live route selection are not complete. Memory does not influence transactional facts. |
| Operational scale | CSV generation/validation reached 100k and 1m counts, and the 100k profile imported into PostgreSQL 16 in 6.508 seconds with zero checked database violations. Million-order PostgreSQL import, k6 mixed API load, latency/resource benchmarks, Redis/Celery queues, live deadline scheduling, and an actual offline copy remain unmeasured. A local Git bundle restore was verified. |
| Frontend browser behavior | Next.js compile and TypeScript build passed. Playwright role journeys, real Keycloak login, and browser-to-API integration remain unverified. |

## Last local checks

```text
.venv/bin/pytest -q                 27 passed (including 1,000 refund properties, OIDC, multi-turn slots, deadline alerts, cross-role HTTP refund, checkpoint turn isolation, strict schema validation, and telemetry masking/filtering/usage)
.venv/bin/python evals/runners/run_smoke.py  25 unique cases, 28 mock executions passed
apps/web: npm run build             compiled, TypeScript passed
data/generator/validate.py realistic-100k-v3  100,000 orders, 0 violations, hashes match
data/generator/validate.py scale-1m-v3       1,000,000 orders, 0 violations, hashes match
alembic upgrade head on SQLite test DB      passed, including refund deadline alerts
alembic upgrade head on fresh PostgreSQL 16  passed; pgvector 0.6.0 enabled
scripts/demo_workflow.py on PostgreSQL 16     one 1,018-cent refund; replay added zero ledger entries
100k COPY into PostgreSQL 16                 6.508 seconds, 17 tables
scripts/verify_postgres.py --expected-orders 100025  passed, six violation counts zero
Docker Compose PostgreSQL 17.11/pgvector 0.8.6  25 orders, one approved refund ledger, six violation counts zero
Docker chat and restart replay               HTTP 200; two fresh findings on each turn; cross-customer HTTP 404
Docker OTel to Jaeger                         seven-span chat trace under resolveai-api
OpenRouter Docker intent call                 one synthetic GPT-4o-mini request answered; schema fix verified
```
