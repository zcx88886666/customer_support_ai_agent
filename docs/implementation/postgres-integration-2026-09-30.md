# PostgreSQL integration check — 2026-09-30

This is measured local evidence for the v6 PostgreSQL path. It does not validate the unrun Docker Compose image, PostgreSQL 17, Keycloak, MCP over HTTP, or pgvector policy retrieval.

## Environment and method

- Ubuntu 24.04 workspace; Docker and a system PostgreSQL install were unavailable.
- Downloaded PostgreSQL **16.15** and pgvector **0.6.0** Ubuntu packages into `/tmp`, extracted them without installing system packages, and ran PostgreSQL as the unprivileged `nobody` user with its data and Unix socket under `/tmp`.
- Used a dedicated `resolveai_test` database for the demo workflow and 100,000-order import, plus an empty `resolveai_fresh` database to verify all migrations from zero. The temporary PostgreSQL server is test infrastructure, not the repository's default service.
- On this extracted-package setup, `extension_destdir=/tmp/resolveai-pg` lets PostgreSQL find pgvector. A normal installation or the planned pgvector container image does not use this temporary path.

## Results

| Check | Actual result |
|---|---|
| Alembic on empty PostgreSQL | Upgraded to `9d24757b98e1`; pgvector extension created automatically |
| pgvector operation in a new session | `[1,2,3] <-> [2,2,3]` returned `1` |
| Synthetic demo seed | 25 orders inserted |
| Customer → warehouse → supervisor → worker | One CNY 1,018-cent simulated refund issued; proposal audit contained `create_proposal`, `approved`, `issue_refund` |
| Identical workflow replay | Same return and proposal IDs; zero additional ledger entries |
| Validated 100,000-order CSV import | PostgreSQL `COPY` completed in **6.508 seconds** after independent CSV validation |
| Imported database totals | 100,025 orders, 125,025 order items, 596,598 shipment events, 2,311 refund ledger rows, including the 25 demo orders and one demo refund |
| Read-only database quality checks | Zero orphan orders, return/customer mismatches, paid-allocation mismatches, over-refunded items, unapproved refunds, or order balance overruns |
| Mock regression after migration changes | 22 Python tests passed; 25 unique smoke cases and 28 executions passed |

The repeatable database check is `DATABASE_URL=<PostgreSQL URL> .venv/bin/python scripts/verify_postgres.py --expected-orders 100025`. The local report was written to `/tmp/resolveai-postgres-report.json`; generated CSVs and the live database are not committed.

## Failures found and fixed

1. The partial older `data/generated/realistic-100k` directory lacked the v3 `customer_profiles.csv` file. Import succeeded using the independently validated `realistic-100k-v3` output. A fresh generation from the current generator also produces the complete schema.
2. The existing `uq_active_policy` Alembic revision used `active IS 1` on PostgreSQL. PostgreSQL rejected it. The migration now uses `active IS TRUE` for PostgreSQL while keeping SQLite's expression.
3. The earlier migrations did not create the pgvector extension. Revision `9d24757b98e1` now creates it on PostgreSQL and remains a no-op on SQLite.
4. This workspace's restricted sandbox blocked local Unix-socket connections and later stalled FastAPI TestClient event loops. The important PostgreSQL and regression commands passed outside that sandbox with permission escalation. The sandbox failures are environment restrictions, not passing test results.

## Remaining PostgreSQL work

- Run the Docker Compose PostgreSQL 17 image and full service stack on a Docker-capable host.
- Add and evaluate versioned policy embeddings, a PostgreSQL full-text/pgvector retrieval index, and its rollback tests. pgvector is enabled and its operator works, but current policy retrieval still uses deterministic lexical/character-gram scoring.
- Measure concurrent return/refund transactions, worker restart recovery, larger imports, and mixed API load. The 100,000-order COPY time is only an import measurement, not an API latency or production capacity claim.
