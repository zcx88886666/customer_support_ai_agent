# PostgreSQL chat statement deadline and safe recovery, 2026-10-04

The chat resource budget now reaches PostgreSQL statements executed through the application's SQLAlchemy engine. Before each statement, the engine sets a transaction-local `statement_timeout` from the remaining request/specialist time. It resets at transaction end and cannot remain on a pooled connection for another customer. Pending chat/audit/thread writes are flushed while the budget is still active.

On PostgreSQL cancellation (`57014`) or exhausted resource admission, chat rolls back the transaction before any recovery. It then gets at most three seconds for a separate handoff transaction: reread thread ownership, omit unverified incoming order/slot references, and create the support ticket/thread state. It retries no business action. If that handoff also times out, the API returns a retriable 503. Other database errors are not presented as successful recovery. Resource reporting preserves the original exhausted account.

Python tests verify rollback even after a return was flushed, zero creation audit/ledger after failure, unchanged order version, safe user retry, and rejection of another customer's thread during the post-rollback ownership check. An additional controlled-clock regression exposed a single-mode scheduling issue: logistics could consume the queued policy task's deadline. Single mode now gathers independent policy evidence first, after the server has verified the order and selected its immutable policy bundle. Stale logistics evidence is still discarded; a timeout no longer removes the already-verified policy result.

## Actual verification

The extended [`verify_specialist_transport.py`](../../scripts/verify_specialist_transport.py) uses real Keycloak authorization and a fresh migrated PostgreSQL database per execution. Its `sql_lock` case holds an actual order row lock while a fully confirmed customer return reaches the API with a 0.8-second resource budget. The blocked transaction is cancelled, the lock holder is released, and two fresh HTTP retries must create only one return, one creation audit, and one order version increment, with zero refunds.

- Focused lock run `evals/reports/20261004T234729Z-transport-f1eebd`: **2/2 cases, 20/20 checks**, approximately 1.03 seconds for each full HTTP timeout/handoff response.
- First broader run `20261004T234909Z-transport-d5f7e6`: **7/8**; single mode lost its queued policy evidence after the slow tool crossed the task deadline. The failed report remains available. The controlled-clock regression reproduced that condition before the scheduling fix.
- Final v2 transport matrix `20261004T235351Z-transport-e6bf38`: **8/8 cases, 80/80 checks**, zero incomplete cases. This includes the six trickling-model, slow/late MCP, and contradictory-snapshot cases from the [earlier resource report](request-resources-2026-10-04.md), plus both SQL-lock modes.
- Real-Keycloak/fresh-PostgreSQL Agent-to-terminal suite `20261004T234912Z-oidc-a78af7`: **30/30**, zero failed/incomplete cases. Worker issuance and replay still respected approval and ledger assertions.
- Final no-key minimum `20261004T235352Z-minimum-9e3c06`: **7/7 suites**, including **150 Python tests**. No provider credit was used by the transport verifier or no-key run.

## Independent review and shared-session fix

Focused independent code review found one additional mock/PostgreSQL concurrency defect: a second specialist could compute its statement timeout before waiting behind the first query on the shared driver connection. A SELECT-only reproduction with a 0.8-second request budget and two 0.6-second sleeps completed both at 0.603 and 1.205 seconds, exceeding the original deadline.

Both specialists now acquire the same Session lock before local ORM reads and deadline calculation, materialize their evidence, and release the lock before model or MCP I/O. A synchronized regression failed with two overlapping Session reads before the fix and passes with one, while requiring both model reviews to overlap. The actual PostgreSQL regression completed the first query at **0.603 seconds**, cancelled the queued query (`57014`) at **0.802 seconds**, and verified rollback, connection reuse, and transaction-local timeout reset. No business data was written. The reviewer rechecked the fix and found no remaining blocker in this slice.

- Agent tests: **36/36**.
- Post-review minimum `20261005T000836Z-minimum-de5dfc`: **7/7 suites**, including **151 Python tests passed** and one opt-in PostgreSQL test skipped. The skipped test passed separately against local Docker PostgreSQL.
- The Docker API was rebuilt again; all **seven** real-OIDC/API/MCP authorization checks passed.

Reproduce the opt-in SELECT-only database test against isolated PostgreSQL:

```bash
DATABASE_DEADLINE_PG_URL='postgresql://resolveai@<isolated-postgres-address>:5432/postgres' \
  .venv/bin/python -m pytest -q -s tests/test_database_deadline.py
```

Run the full matrix with the README's local Keycloak accounts configured, or select the lock cases:

```bash
export SPECIALIST_TRANSPORT_PG_ADMIN_URL='postgresql://resolveai@<isolated-postgres-address>:5432/postgres'
.venv/bin/python scripts/verify_specialist_transport.py --scenario sql_lock
```

Statements through this engine are bounded; connection establishment, pool admission, and separate direct-driver checkpoint operations are not covered by this hook. The handoff has its own short recovery allowance. This is not a universal 25-second service-level guarantee. The locked release gate and other v6 implementation/review items remain open.

## Local Docker deployment

The local API was rebuilt with `docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api`. The subsequent `.venv/bin/python scripts/verify_oidc.py` passed all seven named real-Keycloak/API/MCP authorization checks, including authenticated agent reads and object/role denials. The existing MCP service retained the already-verified stateless JSON configuration.
