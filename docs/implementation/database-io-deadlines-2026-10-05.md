# PostgreSQL connection and checkpoint deadlines, 2026-10-05

Chat's shared RequestBudget now bounds PostgreSQL pool admission, connection establishment and direct parent checkpoint I/O, in addition to the existing SQL statement hook. Each queued caller computes its own remaining wait without mutating the shared pool timeout. A four-slot connection guard runs only libpq connection establishment in daemon threads, propagates the budget and closes any connection returned after caller expiry. Business SQL never runs in those threads.

The locked psycopg3.3.6 connection adapter supplies the remaining float deadline to socket waits. On expiry it closes the connection and raises QueryCanceled (`57014`), allowing SQLAlchemy to invalidate it. Parent PostgresSaver connections also receive a server statement_timeout, a caller-bounded serialization lock, and direct-driver cancellation translation to BudgetExceeded. Connection cleanup closes locally rather than initiating another commit/rollback exchange.

Actual checkpoint lock testing exposed an expired-budget rollback failure: the old chat handler tried network rollback after its allowance ended. PostgreSQL handoff now uses public Session.invalidate(), discards pending ORM state, and opens a fresh transaction for the existing ownership-checked three-second handoff. Local SQLite retains rollback; a separate failing regression proved that invalidating an in-memory connection would delete its fixture database. No business action is retried during handoff.

## Measured verification

- Eleven focused adapter/checkpoint tests passed; pool callers with different deadlines, connection saturation/late cleanup, inherited budget, unexpected error identity, socket timeout, saver lock, direct cancellation translation, transaction discard and in-memory SQLite survival are covered. Full agent plus adapter/checkpoint focus: **47 passed**.
- Final owned Docker PostgreSQL run `20261005T081505Z-db-deadline-a1be65`: **19/19 checks**, zero return/proposal/approval/refund mutations, exactly one owned handoff ticket, coherent fresh checkpoint replay, and successful owned container/volume removal.
- Under a0.20-second budget, pool admission, connected socket wait, checkpoint read/write locks and connection handshake each ended at **0.2001 seconds**; the business statement ended at0.2002seconds. Actual chat checkpoint timeout plus safe handoff finished in **0.2615 seconds** under a0.25-second agent budget and separate recovery allowance.
- Real Keycloak HTTP SQL-lock/retry replay `20261005T081446Z-transport-a9a148`: **2/2 modes**, zero failed/incomplete cases. Retries still created one return without a refund.
- Full Python suite: **255 passed, four optional PostgreSQL/Docker skips**, in24.11seconds. Syntax compilation and git diff check passed.
- Initial failed run `20261005T080819Z-db-deadline-8b89ab` preserves the actual rollback traceback, incomplete report and stopped owned server `ra_serverkill_7e61a7a3566f4583` with its owned data volume. Corrected run `20261005T081056Z-db-deadline-397897` also passed19checks before the SQLite compatibility refinement.

Final Docker rebuild, one worker pong and seven OIDC/API/MCP checks passed. Fresh review found no Critical or Important issue and independently passed all eleven focused tests. One Minor attribution gap is recorded for later work: the probe hashes four adapter/verifier files but omits the changed agent.py handoff implementation; its baseline HEAD therefore does not fully identify that uncommitted handoff source. The measured19/19 results remain valid. The reviewer declined new Docker/database/model/Cloud actions, deployment reruns and repeat review; the documented DNS-slot, startup/final-commit and ambiguous-checkpoint limits were accepted.

```bash
PROMPT_RELEASE=specialists-dev-v1 .venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_database_io_deadlines.py
```

## Practical limits

The caller wait and number of outstanding connector calls are bounded. A resolver stuck inside synchronous libpq cannot be forcibly killed portably; it may occupy one of four connector slots until recovery or process restart. Late results close and cannot execute SQL. Normal no-budget engine connections default to a five-second libpq connect timeout; explicit startup checkpoint migrations remain outside this request contract.

The adapter uses SQLAlchemy2.x's internal queue-class extension and psycopg's inspected wait implementation; focused tests and the locked dependency guard those assumptions. See the official [SQLAlchemy pooling documentation](https://docs.sqlalchemy.org/en/20/core/pooling.html) and [psycopg connection subclass example](https://www.psycopg.org/psycopg3/docs/advanced/typing.html).

A checkpoint commit may already be durable when the client stops waiting. Fresh checkpoint replay and domain idempotency remain necessary. These tests cover the active agent budget and parent saver; startup migrations, separate API final commit, operating-system scheduling and total recovery time do not establish a universal HTTP latency SLA. No provider credits or Cloud exports were used, and the locked release gate remains open.
