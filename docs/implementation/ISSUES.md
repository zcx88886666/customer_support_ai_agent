# Implementation and verification log

Updated 2026-09-29. This log records encountered failures, fixes, and open integration gates. It is not a claim that the v6 plan is fully finished.

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

## Open gates needing external runtime, credentials, or further implementation

| Gate | Current evidence and next concrete action |
|---|---|
| Docker/PostgreSQL/Keycloak/Jaeger integration | `docker`/`podman` are unavailable here. Compose, migrations, realm import, MCP, OIDC claims, PostgresSaver, OTLP export, PostgreSQL COPY, refund worker concurrency, and container restart recovery need a Docker-capable Ubuntu run. SQLite migrations were upgraded successfully. |
| Langfuse Cloud | No project name, public/secret keys, or base URL were provided. Local inference and eval do not depend on Cloud. With keys, run mirror sync/drift check, an eval, export reconciliation, masking canary, dashboard setup, and quota calibration. |
| OpenRouter real-model path | No key or live model budget was provided. The configured structured intent adapter is unmeasured. Run provider/schema trials, fix provider version, then paired locked evaluations. |
| Locked evaluation and human review | The 25 smoke cases are original synthetic development fixtures. The planned 30/30/20/20 minimum and expanded suites, two-person critical review, mutation/property suite, paired model comparison, and external benchmarks remain to be built and reviewed. Do not treat 28/28 mock executions as a production success rate. |
| Policy retrieval | The no-key retrieval is deterministic lexical/character-gram RRF. PostgreSQL full-text + pgvector embedding indexes, index versioning, and historical policy publication/rollback regression suite remain open. |
| Memory A/B | Confirmed preferences, consent, correction, deletion, and ownership are tested. PostgresStore and Mem0 OSS A/B with 40 multi-session stories and live route selection are not complete. Memory does not influence transactional facts. |
| Operational scale | CSV generation/validation reached the planned 100k and 1m counts. PostgreSQL import, k6 mixed API load, latency/resource benchmarks, Redis/Celery queues, and a backup restore drill are not measured. |
| Frontend browser behavior | Next.js compile and TypeScript build passed. Playwright role journeys, real Keycloak login, and browser-to-API integration remain unverified. |

## Last local checks

```text
.venv/bin/pytest -q                 20 passed (including 1,000 refund properties, OIDC, and multi-turn slots)
.venv/bin/python evals/runners/run_smoke.py  25 unique cases, 28 mock executions passed
apps/web: npm run build             compiled, TypeScript passed
data/generator/validate.py realistic-100k-v3  100,000 orders, 0 violations, hashes match
data/generator/validate.py scale-1m-v3       1,000,000 orders, 0 violations, hashes match
alembic upgrade head on SQLite test DB      passed
```
