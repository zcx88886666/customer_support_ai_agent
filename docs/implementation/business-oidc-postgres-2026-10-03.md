# Real OIDC and PostgreSQL business evaluation — 2026-10-03

The [OIDC/PostgreSQL runner](../../evals/runners/run_business_oidc_postgres.py) replayed all six [business-v2](../../evals/datasets/business_workflows_v2.jsonl) terminal-state cases. For each case it created a fresh isolated Docker PostgreSQL 17 database, applied the current Alembic migrations, seeded the synthetic demo world, started a separate API process with `AUTH_MODE=oidc` and `LONG_TERM_MEMORY_MODE=postgres_store`, and ran refund worker steps in fresh processes. It obtained real Keycloak authorization-code/PKCE tokens from the ignored local synthetic account file. OpenRouter and Langfuse calls were disabled for this rule/authorization evaluation. The scorer read PostgreSQL terminal state and audit through a separate session after the HTTP and worker steps.

| Case | Terminal result | HTTP and worker step latency |
|---|---:|---:|
| Approved refund and replay | Pass | 958.72 ms |
| Failed warehouse inspection | Pass | 354.11 ms |
| Stale proposal and regeneration | Pass | 964.48 ms |
| Foreign customer denial | Pass | 36.33 ms |
| Missing explicit confirmation | Pass | 33.39 ms |
| Expired return window | Pass | 40.80 ms |

Overall: **6/6 passed**, zero failed or incomplete cases. Every case had zero failed scorer checks. The report and bounded API logs are under ignored `evals/reports/20261004T004706Z-oidc-ba91bd/`; the six synthetic databases remain on the local PostgreSQL server for inspection. These latencies cover the case's HTTP and worker steps after API startup and seeding, not end-to-end database provisioning or sustained load.

The first one-case pilot could not import the existing Keycloak login helper when invoked as a script. Adding the repository root to that runner's import path resolved it; the approved-refund pilot and then the six-case run passed. The existing mock-auth SQLite business tests and targeted mutation gate passed after the shared step executor accepted either mock headers or real Bearer tokens. No live demo orders or production customer data were changed.

Reproduce after starting the local Keycloak and PostgreSQL Compose services and configuring the synthetic demo accounts:

```bash
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<docker-postgres-host>:5432/postgres .venv/bin/python evals/runners/run_business_oidc_postgres.py
```

The runner rejects a URL that does not target the PostgreSQL `postgres` administrative database. It creates new `ra_biz_oidc_*` databases and does not delete them automatically. This six-case synthetic development run does not satisfy the planned human-reviewed 30/30/20/20 locked evaluation or measure real-model agent quality.
