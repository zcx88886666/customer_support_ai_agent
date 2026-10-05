# Database connection and checkpoint deadlines implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make database pool admission, connection establishment and parent checkpoint I/O respect the shared chat deadline.

**Architecture:** A request-aware SQLAlchemy queue adapter bounds each caller's pool wait without changing shared pool settings. A four-slot daemon connection guard bounds caller waiting even during DNS/connect stalls and closes late connections. A psycopg connection subclass bounds socket waits, closing connections with unknown state on expiry; parent savers use bounded locks and a server statement timeout. Existing SQL authorization, rollback and handoff remain authoritative.

**Tech Stack:** SQLAlchemy2.x QueuePool, psycopg3.3.6+, LangGraph PostgresSaver, existing RequestBudget.

**Spec:** v6 §§3,5,7,10 and the recorded connection/checkpoint gap in `docs/implementation/ISSUES.md`. This refines existing request resource controls. The user authorized autonomous implementation without interactive checkpoints; native execution continues on local master.

## Constraints

- Connection threads run only connection establishment, never business statements. At most four pending calls; caller timeout closes any late result, and stalled DNS may occupy a slot until recovery/process restart.
- Expired socket waits close the connection; never return an uncertain transaction to a pool. Checkpoint timeouts become BudgetExceeded and trigger the existing rollback/handoff path.
- Normal no-budget operators retain standard pooling; connect timeout defaults to five seconds. Startup checkpoint migration remains outside request deadlines.
- Keep SQLAlchemy's private queue adapter small with direct contention regressions; use the locked psycopg version whose wait method supports a timeout, raising the dependency floor to3.3.6.
- No paid models, Cloud exports or automatic push. Disposable migrated PostgreSQL probe only; preserve failure evidence.

## Review focus

- Different concurrent callers must never change each other's pool allowance.
- Expiry during DNS/connect must not publish a late connection or grow threads without a bound.
- Checkpoint locks, reads, batched writes and cleanup must not extend the shared deadline.
- Closing an expired connection must permit SQLAlchemy rollback and a fresh owned handoff; unexpected SQL errors must retain their identity.
- Background LangGraph checkpoint calls must inherit the deadline, and fresh replay must still load coherent checkpoints.

### Task 1: Request-aware database I/O adapters

**Files:** Create `apps/api/resolveai/database_io.py`, `tests/test_database_io.py`, `tests/test_checkpoint_deadline.py`, isolated verification script and implementation report. Modify `db.py`, `checkpoint.py`, `agent.py`, pyproject/lock, README/STATUS/ISSUES.

**Interfaces:** `BudgetQueuePool`, `BudgetConnection`, `connect_with_deadline(*args, **kwargs)`, `BudgetLock`; engine uses public `do_connect` and `poolclass` hooks, parent saver receives the guarded connection and timed lock.

- [x] Write failing contention and late-connect cleanup tests, socket timeout/invalidation tests, and checkpoint timeout/handoff/replay regressions. Expected: fail before the adapters exist.
- [x] Implement minimal adapters and wire engine/saver; run focused tests. Expected: no connection leak, no shared timeout mutation, correct deadline exceptions.
- [ ] Run real isolated PostgreSQL pool contention, checkpoint row lock, socket stall and fresh graph replay checks, plus full Python tests and rebuilt Docker OIDC/readiness. Expected: bounded waits, no unauthorized business mutations and reusable/fresh connections.
- [ ] Obtain one fresh read-only final review; fix Critical/Important findings once with red→green tests.
- [ ] Record measured limits and logs, diff check and explicit local commits.

## Rulings

- Ruling: DNS cancellation is not portable in synchronous libpq; bound the caller and outstanding connector count, close late connections, and disclose occupied-slot recovery. Cost: a truly stuck resolver may occupy one of four slots until process restart.
- Ruling: Use psycopg's socket-wait timeout and close on expiry, rather than abandoning running SQL in background threads. Cost: a timed-out checkpoint commit can be durable; normal checkpoint/business idempotency governs replay.
- Ruling: Server statement_timeout caps checkpoint work as well as client waiting. Migration/setup remains an explicit startup operation, not a request.

- Ruling: Once I/O allowance is exhausted, discard the Session through public `invalidate()` instead of a network rollback — the real checkpoint-lock probe reproduced rollback failing under the expired budget. The driver closes locally; server rollback and fresh ownership revalidation govern the handoff. Cost: a healthy pooled connection is replaced on each resource-limit handoff.

- Ruling: Preserve local SQLite rollback to retain in-memory worlds; only PostgreSQL uses local invalidation on expiry. Memory fixture regression RED→GREEN.
