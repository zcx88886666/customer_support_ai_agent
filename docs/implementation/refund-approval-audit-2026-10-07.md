# Refund approval audit attribution — 2026-10-07

The database-first refund scorer now requires one `approved` audit linked to the refunded proposal, with entity type `proposal` and an actor matching the persisted `Approval.actor_id`. Previously, a complete ledger and approval with an approval audit attributed to another actor still passed `refund_authorized`. This change affects local evaluation only. The API's authenticated supervisor role check remains the authority for whether that actor may approve.

The development mutation runner now replaces the actor on the `approved` audit with another synthetic customer while leaving the approval and refund intact. The shared scorer rejects that fault. The targeted campaign now has thirteen named application faults; this is not a general mutation-coverage percentage.

Verification:

- The focused scorer regression failed before the change because `refund_authorized` was true for the wrong-actor audit, then passed after the change.
- `PYTHONPATH=apps/api:. .venv/bin/pytest -q tests/test_business_mutations.py tests/test_eval_scorer.py`: **10 passed**; the new fault failed `refund_authorized`, with **13 killed, zero survived, zero invalid** overall.
- `PYTHONPATH=apps/api:. .venv/bin/pytest -q`: **298 passed, five optional skips**, with nine existing Alembic configuration deprecation warnings.
- `PYTHONPATH=apps/api:. .venv/bin/python scripts/verify_minimum.py`: **7/7** development suites passed, report `20261007T102306Z-minimum-4816ac`; `locked_release_pass=false`.
- `BUSINESS_PG_ADMIN_URL=postgresql://resolveai@172.19.0.2:5432/postgres PYTHONPATH=apps/api:. .venv/bin/python evals/runners/run_business_oidc_postgres.py --case-id business-approved-refund`: **1/1** with real Keycloak tokens and a fresh migrated PostgreSQL database, report `20261007T102255Z-oidc-2e8691`.
- Read-only review found no Critical or Important issue. It noted that matching audit and approval actors does not independently prove supervisor role; separate API authorization tests cover that boundary.

The fixtures are author-written development data. Independent human gold review and the locked release gate remain open. No model provider or Langfuse call was made.
