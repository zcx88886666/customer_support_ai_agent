# ResolveAI local operations runbook

> Verified scope: the synthetic local Docker Compose development stack. See [implementation status](STATUS.md) for dated test results and [the threat model](SECURITY_THREAT_MODEL.md) for trust boundaries. This is not a production deployment or disaster-recovery guarantee.

## Start and inspect

From the repository root:

```bash
docker compose -f infra/compose/compose.yaml up --build -d
docker compose -f infra/compose/compose.yaml ps
docker compose -f infra/compose/compose.yaml exec -T api python scripts/verify_postgres.py --expected-orders 25
docker compose -f infra/compose/compose.yaml logs --tail=100 api worker beat
```

The API health endpoint is `http://127.0.0.1:8000/health`; the web entry point is `http://127.0.0.1:3000`; Jaeger is `http://127.0.0.1:16686`. The default stack uses local mock actors; use the documented [Keycloak OIDC setup](../README.md#containers-oidc-and-external-integrations) for authenticated role demonstrations. Keep `.env` values out of shell transcripts and reports.

## Job and data checks

Beat queues refund, deadline and policy-index checks on their configured schedules. SQL approvals and the ledger remain authoritative if Redis or a worker is unavailable. After worker recovery, inspect the worker log and the supervisor proposal/deadline views; replaying the worker must not create a second ledger. The isolated [Celery probe](implementation/celery-jobs-2026-10-05.md) covers duplicate deliveries, worker restart and a broker outage. The queued refund scan is bounded to 100 proposal candidates per task and continues with a cursor when full.

Synthetic bulk generation is operator initiated with `scripts/enqueue_generated_world.py` on the separate one-child bulk worker, as shown in the [README](../README.md#business-workflow). Its named output appears under ignored `data/generated/` only after validation. Inspect the `data_quality_report.json` there for `violation_count: 0` and `file_hashes_match: true`. Large profiles need local disk capacity; they are not part of normal startup.

The same bulk worker accepts only the six fixed no-key development suites through `scripts/enqueue_development_eval.py`. Each run uses a temporary SQLite URL and writes a task summary under ignored `evals/reports/queued/`; see the README for the command. It does not upload traces or gold, and it does not change the locked release gate.

## Reproducible verification

Run `PYTHONPATH=apps/api:. .venv/bin/python scripts/verify_minimum.py` for the seven no-key development checks. The resulting ignored `evals/reports/<run_id>/summary.json` is the local gate input; `locked_release_pass` remains false until independently reviewed gold and a frozen locked run exist. Run the isolated scripts named in [status](STATUS.md) when changing OIDC, PostgreSQL recovery, Redis/Celery or telemetry boundaries. Export only the essential, masked Langfuse traces for a deliberate evaluation; normal Cloud tracing follows the [README configuration](../README.md#containers-oidc-and-external-integrations).

## Recovery and rollback boundary

Local Git bundles and PostgreSQL logical archives have been restored in **disposable** fixtures. The [backup/restore drill](implementation/postgres-backup-restore-2026-10-05.md) also rejects a truncated archive; the [PostgreSQL server-crash drill](implementation/postgres-server-crash-2026-10-05.md) verifies WAL rollback of uncommitted refund actions. Those drills do not constitute an offline copy of the current Compose data volume.

For a code regression, preserve the database and report artifacts, identify a known-good committed revision and its migration level, and rehearse the older image against a restored disposable database before switching the running API or worker. Do not assume an Alembic downgrade can safely undo newly committed business writes. Policy content has its own audited supervisor rollback endpoint, `POST /policies/{bundle_id}/rollback`, with hash/index verification. A production rollback and historical-backup retention policy require an explicit owner decision and a separate restore drill on the intended storage medium.

## Escalation evidence

For a wrong customer view, disputed return, stuck approval or alleged refund, record the synthetic case ID or customer-owned business ID, time window, HTTP status, database terminal state and audit action IDs. Use Jaeger trace IDs and only essential masked Cloud observations as supporting diagnostics. A chat answer or Cloud score cannot prove a refund occurred. An unresolved exception remains a human support ticket; support resolution does not override warehouse, supervisor or ledger gates.
