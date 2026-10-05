# Disposable PostgreSQL server crash verification, 2026-10-05

[`verify_postgres_server_crash.py`](../../scripts/verify_postgres_server_crash.py) runs the actual return/refund domain paths against a new Docker PostgreSQL instance. It uses only the cached `pgvector/pgvector:pg17` image, pins its immutable ID, creates a labeled private volume, and publishes an explicit loopback port selected by the OS. Container ID, run labels, synthetic label, sole volume mount and loopback binding are checked before every kill or cleanup. The shared Compose database is never selected.

The underlying [client-kill verifier](transaction-kill-2026-10-04.md) now accepts an optional server-crash callback after another connection observes the flushed open transaction and cannot see its uncommitted ledger or action audit. The callback verifies durability settings, sends SIGKILL to the owned PostgreSQL container, requires exit code 137, and restarts the same container/volume. Startup logs must show interruption, automatic WAL recovery and readiness. Recovery checks use fresh connections/processes, and backend disappearance matches PID plus application name to tolerate PID reuse.

## Actual evidence

- `20261005T010202Z-serverkill-b4dfb6`: **2/2 cases, 43/43 checks**, zero incomplete cases. Both server restarts logged WAL recovery; measured kill-to-query-ready durations were **1.2097** and **1.2364 seconds**. These two timings are development observations, not an operational SLA.
- Return: the uncommitted return/audit/version changes disappeared; two fresh-process retries returned the same request ID and one creation audit, with no refund.
- Approved refund: the earlier committed approval survived; uncommitted ledger, audit, balance and version changes disappeared. A new worker issued one correct ledger; the next issued none. Paid amount, refunded quantity, version and issuance audit agreed.
- Original client-kill regression `20261005T010307Z-txkill-f02c42`: **2/2**, unchanged **29/29 business checks**. Its fresh synthetic databases remain locally for inspection.
- Ownership/diagnostic tests: **14/14**. Unknown resource identities, missing/mismatched labels, different/bind mounts, exposed or restart-unstable ports are refused. Setup failure logs preserve known categories and exit codes without raw output; the two new diagnostic regressions failed before that change and passed afterward.
- The existing deployed API passed all **seven real-OIDC authorization checks** after the isolated server test.
- Final Python suite after the diagnostic fix: **197 passed, one opt-in PostgreSQL deadline test skipped**, in 21.61 seconds. That deadline test was verified separately in its earlier slice.

The first run `20261005T005933Z-serverkill-7ad2f6` is retained as incomplete: PostgreSQL recovered successfully, but Docker reassigned an unspecified host port and the verifier kept its old URL. Two controlled restarts of that owned fixture demonstrated port `44622` becoming `44623`. An explicit binding fixes the cause; its guard regression went red→green. The failed container `ra_serverkill_e6251f31a16d46e2` remains stopped with volume `ra_serverkill_e6251f31a16d46e2_data` for inspection. The successful run removed only its verified owned container/volume.

One fresh read-only reviewer inspected the guards, callback ordering, actual report and twelve initial ownership tests. No Critical/Important findings; one diagnostic issue was upgraded by the author because the user explicitly requested usable logs, then reproduced and fixed. No findings remain deferred. Hardware power loss, backup restoration, production traffic and OIDC/model behavior were explicitly outside that review and this verifier's claims.

## Reproduce

Docker and the cached image must already exist:

```bash
.venv/bin/python scripts/verify_postgres_server_crash.py
```

No database URL, API key or Cloud configuration is needed. Only synthetic fixtures use loopback trust authentication. Local manifests record both verifier hashes, Git commit, exact image/container IDs and cleanup state. JSONL, summary, HTML, migration/process/server/recovery logs and safe error locations remain under ignored `evals/reports/<run_id>/`. A setup port collision fails safely and preserves diagnostics rather than connecting to another server.

This verifies **server-process crash and WAL recovery**, with storage intact. It does not verify hardware power loss, damaged storage, backup restoration or an offline copy. Independent human gold and the locked release gate remain open.
