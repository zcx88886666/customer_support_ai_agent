# Langfuse outage business replay — 2026-10-03

The real-OIDC/PostgreSQL business runner now accepts `--langfuse-outage`. It enables the API and worker Langfuse exporters with **fake** project keys, points them to a closed `127.0.0.1:9` port, and rejects the run if that port is reachable. OpenRouter stays disabled. Each case uses a fresh migrated synthetic PostgreSQL database and separate API/worker processes; the scorer reads database terminal state and audit after HTTP actions.

One `business-approved-refund` replay passed **17/17** terminal checks: return ownership and idempotency, warehouse receipt/inspection, proposal and supervisor decision, approval before refund, exact one authorized ledger, correct amount, and zero extra issuance on worker replay. All six HTTP steps returned 200. The endpoint check found port 9 unreachable, and the isolated API log reported `Failed to export spans batch due to timeout, max retries or shutdown.` The business steps took 7,796.78 ms after API startup; the separate six-second wait gave the batch exporter time to attempt upload and is excluded from that timing. The ignored report is `evals/reports/20261004T022150Z-oidc-61041e/`, and the synthetic database is `ra_biz_oidc_20261004_022150_61041e_1`.

This proves the tested refund path continued through a failed Langfuse upload. It does not establish behavior for every Cloud failure mode or a sustained outage. The local scorer and PostgreSQL audit, rather than a Cloud score, determined the result.

Reproduce with local Compose PostgreSQL and Keycloak running and the synthetic OIDC accounts configured:

```bash
BUSINESS_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres \
  .venv/bin/python evals/runners/run_business_oidc_postgres.py \
  --case-id business-approved-refund --langfuse-outage
```
