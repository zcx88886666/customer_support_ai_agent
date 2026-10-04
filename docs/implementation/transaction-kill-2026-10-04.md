# PostgreSQL transaction-interior client crash verification, 2026-10-04

The verifier [`verify_transaction_kill_postgres.py`](../../scripts/verify_transaction_kill_postgres.py) creates a fresh migrated synthetic PostgreSQL database for each of two cases. A disposable Python process executes the real domain action and flushes all SQL writes without committing. The parent observes `pg_stat_activity` reporting an open, idle transaction, verifies that another connection cannot see its ledger or action audit, then sends SIGKILL to that owned child. No production crash switch or live demo database is used.

The final report is `evals/reports/20261004T231648Z-txkill-a6c5d0/`: **2/2 cases, 29/29 checks**, zero incomplete cases. The earlier run `20261004T231605Z-txkill-d759c3` passed the same checks before recovery-log improvements. The isolated databases remain locally for inspection:

| Case | Database | Verified outcome |
|---|---|---|
| Return before commit | `ra_txkill_65676f371c4e4bc1` | No return or creation audit survived the kill; order version and paid balance stayed unchanged. Two fresh-process retries produced the same return ID, one creation audit, no ledger, and one version increment. |
| Approved refund before commit | `ra_txkill_2ffde31b144241c4` | Approval survived from its earlier committed transaction; uncommitted ledger, issuance audit, balance, and version changes rolled back. A fresh worker issued exactly one ledger; another worker issued none. Amount, quantity, order version, and issuance audit agreed. |

Manifest, case JSONL, summary, HTML, migration logs, child process logs, and recovery logs are stored in the ignored report directory. Failure reports preserve exception type and stack locations without recording connection strings or environment values. The run manifest records the source commit and exact verifier SHA-256 because verification precedes its commit.

```bash
export TRANSACTION_KILL_PG_ADMIN_URL='postgresql://resolveai@<isolated-postgres-address>:5432/postgres'
.venv/bin/python scripts/verify_transaction_kill_postgres.py
```

This verifies abrupt **application client death during a transaction** against Docker PostgreSQL 17. It does not simulate PostgreSQL server death, storage failure, or production traffic. The release gate remains false. Separately, `.venv/bin/python -m pytest -q` passed **135/135 in 19.00 seconds** outside the restricted sandbox; the earlier AnyIO portal hang did not reproduce there.
