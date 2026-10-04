# Durable supervisor approval interrupt/resume — 2026-10-03

The PostgreSQL API now creates a [LangGraph approval wait checkpoint](../../apps/api/resolveai/approval_checkpoint.py) after a warehouse proposal commits. The supervisor decision endpoint first commits the authenticated domain decision, then resumes the graph. The resume payload carries no authority: the graph rereads the proposal, approval row, return, order version, and ledger state from SQL. A resume without a committed approval re-enters the wait; it cannot issue a refund. The existing domain service and worker still independently enforce supervisor role, inspection, plan/policy/order versions, amount, and idempotency. SQLite no-key mode continues to use the SQL state machine without a durable LangGraph checkpoint.

A proposal or decision can commit before its graph checkpoint call fails. Both endpoints remain idempotent: repeating a proposal can create the missing wait, and repeating a supervisor decision can create and resume a missing wait. When a replacement proposal makes an older one stale, the API resumes the older checkpoint to a terminal `stale` state. The checkpoint stores the proposal ID and verified outcome, not a supervisor token or authorization claim.

Two new Python tests exercised interrupt, forged `Command(resume=...)` payload, still-pending reinterrupt, committed approval, stale replacement, rejection, and zero ledger entries before the worker. The full Python suite passed **70/70**. The existing six-case business-v2 scorer was also replayed selectively against fresh migrated PostgreSQL databases with real Keycloak tokens:

| Real-OIDC isolated case | Outcome | Checkpoint inspection |
|---|---|---|
| Approved refund, `20261004T013025Z-oidc-3a322e` | Passed; one authorized ledger | Proposal checkpoint `approved`, no next node; SQL proposal later `issued` |
| Stale proposal after reconciliation, `20261004T013206Z-oidc-2cb2f7` | Passed; old decision HTTP 409, fresh approval issued once | Old checkpoint `stale`, replacement `approved`, both with no next node |
| API process restarted before supervisor decision, `20261004T013614Z-oidc-90ea2f` | Passed; restart flag true, one authorized ledger | Persisted checkpoint `approved`, no next node after a new API process resumed it |

The first stale-proposal replay passed its business scorer but showed the old checkpoint still suspended. Adding stale-checkpoint reconciliation closed that gap, and the second replay confirmed it. The runner's restart mode stopped the first Uvicorn process after the proposal and started a fresh process on the same port before the supervisor request; its terminal SQL checks and a separate checkpoint inspection passed. Reports and API logs are under ignored `evals/reports/<run_id>/`; the synthetic databases remain locally for inspection. The Docker API image was rebuilt after the final code change and `/health` returned 200. No OpenRouter or Langfuse call was needed for these business replays.

An additional [post-commit failure verifier](../../scripts/verify_approval_checkpoint_failure_postgres.py) used a fresh synthetic database `ra_approval_recovery_ad5955523a42`. It injected one exception after proposal commit but before wait-checkpoint creation, and one after supervisor decision commit but before resume. Both HTTP calls returned 500. Retrying the same idempotent endpoints restored the pending and completed checkpoints. The worker then issued exactly one correct ledger row; a second worker run issued none. **5/5 checks passed.** The verifier's first attempt reached the worker check but failed because it expected a dictionary instead of the worker's actual list return; the assertion was corrected and the clean rerun passed.

Reproduce the focused checks:

```bash
.venv/bin/pytest -q
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python evals/runners/run_business_oidc_postgres.py --case-id business-approved-refund
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python evals/runners/run_business_oidc_postgres.py --case-id business-stale-proposal
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python evals/runners/run_business_oidc_postgres.py --case-id business-approved-refund --restart-before-decision
APPROVAL_RECOVERY_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python scripts/verify_approval_checkpoint_failure_postgres.py
docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api
```

These checks establish a durable PostgreSQL checkpoint across committed HTTP requests, an API process restart, and injected post-commit checkpoint failures. An actual process kill during a checkpoint write remains a narrower recovery check. The checkpoint outcome is observational; it does not replace the SQL approval and refund ledger as the money authority.
