# Revoked preference value erasure — 2026-10-03

Preference deletion and consent withdrawal now blank the structured `memory_entries.value` in the same transaction that marks each entry revoked and removes its PostgresStore item. The row ID, key, revoked flag, and separate audit event remain for workflow integrity; the old preference text is no longer stored in that row. A new [Alembic revision](../../infra/migrations/versions/7c461acdb21e_erase_revoked_memory_values.py) scrubs values from previously revoked rows. Its downgrade cannot restore erased text.

An [isolated PostgreSQL migration verifier](../../scripts/verify_memory_erasure_migration.py) upgraded to the prior revision, inserted a synthetic revoked value and a separate active value, upgraded to head, and passed 3/3 checks: revoked value blank, active value preserved, row identity preserved. A fresh SQLite database also migrated to head. The rebuilt Docker API applied revision `7c461acdb21e` to the synthetic demo database. Aggregate inspection, without displaying values, found two revoked rows with two nonempty values before migration and two revoked rows with **zero** nonempty values afterward; `/health` returned 200.

The real Keycloak OIDC memory verifier passed four role/consent/correction/deletion/restoration checks. A fresh PostgreSQL write-load run (`evals/reports/20261004T020211Z-memory-load-461caa/`, ignored) passed 320 independent-customer writes and guarded reads plus 200 same-customer corrections, then verified 17 revoked SQL rows all had blank values and 17 PostgresStore namespaces were empty. The full Python suite passed **71/71**. The isolated migration database is `ra_memory_erase_3f2ce523b74c`; the isolated load database is `ra_memory_load_ab9902c834b6`. Both contain synthetic data only and remain for inspection.

```bash
MEMORY_ERASURE_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python scripts/verify_memory_erasure_migration.py
DATABASE_URL=sqlite:////tmp/resolveai-memory-erasure-migration.sqlite .venv/bin/alembic upgrade head
.venv/bin/pytest -q
```

This removes revoked text from the current structured SQL row and PostgresStore. It does not retroactively erase old backups, exported traces, or historical evaluation artifacts; those require a separate retention review. The migration runs only once per database and is intentionally irreversible for the erased value.
