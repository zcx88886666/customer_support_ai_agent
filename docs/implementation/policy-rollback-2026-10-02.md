# Historical policy rollback verification — 2026-10-02

Supervisors can reactivate a superseded policy with `POST /policies/{bundle_id}/rollback`. The service verifies the historical bundle's content hash, refreshes its PostgreSQL search rows, deactivates the current bundle, and records a `rollback_policy` audit event with the previous active ID. The switch remains one database transaction. Activation also refuses a verified bundle whose search index is stale or missing.

The original synthetic `policy-demo-v1` used a fixed marker hash. Revision `8a512e96af34` repairs only that known fixture when its four clauses exactly match the committed seed. New seeds calculate the content hash directly. The repaired hash is retained on downgrade so a future rollback does not become unverifiable.

Verified results:

```text
.venv/bin/pytest -q                         41 passed in 4.82s
evals/runners/run_smoke.py                  25 cases, 28/28 executions passed
fresh SQLite legacy-hash migration          repaired; four clauses match hash
Docker PostgreSQL head                      8a512e96af34
Docker demo policy hash                     matches all four stored clauses
Docker policy-search verifier              draft indexing, bundle scope,
                                           stale-index rejection, rollback passed
scripts/verify_oidc.py                      seven authenticated checks passed
```

The PostgreSQL verifier performs its policy activation and rollback inside a transaction that it rolls back afterward, leaving the demo active bundle unchanged. Unit tests also cover hash drift rejection and the supervisor-only HTTP endpoint. This is synthetic policy rollback; historical policy selection for existing orders still follows each order's stored bundle ID.
