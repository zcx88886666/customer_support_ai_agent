# Refund approval chronology scorer — 2026-10-07

The local database-first evaluation now requires the persisted supervisor approval timestamp to be no later than the simulated refund ledger timestamp. This closes a scorer gap: an otherwise coherent ledger with an approval recorded after issuance previously satisfied `refund_authorized`. The comparison normalizes SQLite's naive UTC values and PostgreSQL's timezone-aware values to instants. Production refund authorization and state transitions were not changed.

The development mutation runner now moves the approval timestamp one day after the worker's issuance on the existing approved-refund fixture. The fault remains a valid application run, and the `refund_authorized` check detects it. This raises the targeted campaign from eleven to twelve named faults; it does not measure general mutation coverage or independent gold quality.

Verification:

- The new scorer regression failed before the change with `assert not checks["refund_authorized"]` because the check returned true; it passed after the change.
- `PYTHONPATH=apps/api:. .venv/bin/pytest -q tests/test_business_mutations.py tests/test_eval_scorer.py`: **9 passed**. The mutation run reported **12 killed, zero survived, zero invalid**; the new fault failed `refund_authorized`.
- `PYTHONPATH=apps/api:. .venv/bin/pytest -q`: **297 passed, five optional skips**, with nine existing Alembic configuration deprecation warnings.
- `PYTHONPATH=apps/api:. .venv/bin/python scripts/verify_minimum.py`: **7/7** development suites passed, report `20261007T101827Z-minimum-a636ea`; `locked_release_pass=false`.
- `BUSINESS_PG_ADMIN_URL=postgresql://resolveai@172.19.0.2:5432/postgres PYTHONPATH=apps/api:. .venv/bin/python evals/runners/run_business_oidc_postgres.py --case-id business-approved-refund`: **1/1** with real Keycloak tokens and a fresh migrated PostgreSQL database, report `20261007T101811Z-oidc-4bd17c`.
- Read-only code review found no Critical or Important issue. It noted that the delayed-approval fault itself has no separate PostgreSQL injection replay; the authenticated PostgreSQL run verifies the valid path through the same scorer.

These are author-written development checks. Human gold review and the locked release gate remain open. No model provider or Langfuse call was made for this slice.
