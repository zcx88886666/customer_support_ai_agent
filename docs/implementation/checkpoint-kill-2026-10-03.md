# API process kill during approval checkpoint — 2026-10-03

The real-Keycloak/OIDC PostgreSQL business runner now accepts `--kill-at-checkpoint proposal` and `--kill-at-checkpoint decision` for the approved-refund case. A **test-only Uvicorn wrapper** patches `PostgresSaver.put` in the disposable API process: it calls the real PostgreSQL write, then sends SIGKILL to its own process before the endpoint can respond. The production API has no crash switch. The runner verifies the process exited with signal 9, reads the committed SQL proposal or supervisor decision, confirms no refund ledger exists yet, starts a fresh API process, and retries the same endpoint. It then runs the worker twice and inspects the final SQL and LangGraph checkpoint.

Both isolated runs passed **20/20** checks. Proposal kill: `20261004T022637Z-oidc-4e9641`; decision kill: `20261004T022648Z-oidc-b0daac`. Each retry returned HTTP 200, the worker issued exactly one authorized refund, and the approval checkpoint ended with status `approved` and no next node. The synthetic databases are `ra_biz_oidc_20261004_022637_4e9641_1` and `ra_biz_oidc_20261004_022648_b0daac_1`; detailed logs and terminal checks are in ignored `evals/reports/<run_id>/`.

This covers a hard process death immediately **after** one low-level checkpoint write. It does not simulate PostgreSQL dying inside its own transaction. SQL proposal/decision and ledger remain the business authority; checkpoint recovery uses the existing idempotent endpoints.

```bash
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres \
  .venv/bin/python evals/runners/run_business_oidc_postgres.py \
  --case-id business-approved-refund --kill-at-checkpoint proposal
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres \
  .venv/bin/python evals/runners/run_business_oidc_postgres.py \
  --case-id business-approved-refund --kill-at-checkpoint decision
```
