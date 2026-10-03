# PostgreSQL refund worker overlap and recovery — 2026-10-03

The refund domain service now locks the order before checking for an existing ledger entry. A second worker can otherwise read an empty ledger while the first worker's transaction is uncommitted, then resume after the lock with stale ORM state. The service refreshes mutable order, proposal, request, and item facts after the lock. The database's unique ledger constraints remain the final duplicate guard.

## Reproduction

Use a **fresh isolated PostgreSQL database**; the verifier rejects one that already has return requests. With the Compose PostgreSQL service running, create a database and run:

```bash
docker compose --env-file .env -f infra/compose/compose.yaml exec -T postgres createdb -U resolveai resolveai_refund_check
docker compose --env-file .env -f infra/compose/compose.yaml build api
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_refund_check -e LANGFUSE_PUBLIC_KEY= -e LANGFUSE_SECRET_KEY= -e OTEL_TRACES_EXPORTER=none api sh -c 'alembic upgrade head && python scripts/verify_refund_concurrency.py'
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_refund_check -e LANGFUSE_PUBLIC_KEY= -e LANGFUSE_SECRET_KEY= -e OTEL_TRACES_EXPORTER=none api python -m resolveai.worker
```

The verifier holds the order row lock until two independent sessions are observed waiting for it in `pg_stat_activity`, then releases the lock. It checks that both calls return one ledger ID, exactly one issuance audit exists, and the paid balance and version advance once. It then flushes another approved refund and rolls the transaction back, checks that no ledger survived, and confirms a new worker invocation recovers the approval. A separate worker process checks that later runs issue nothing.

## Actual result

On Docker PostgreSQL 17.11, the final isolated run printed `overlapping_workers=2`, `overlap_ledger_count=1`, `issue_audit_count=1`, `rollback_recovered=true`, and `final_ledger_count=2`. The subsequent separate process printed `issued_ledger_ids=[]`. The Python suite passed **41 tests**. The test uses only synthetic orders, a short controlled overlap, and a transaction rollback to model a pre-commit process death. It does not test a container kill during arbitrary I/O, post-commit acknowledgement loss, or a sustained write workload. The live demo database was not used.
