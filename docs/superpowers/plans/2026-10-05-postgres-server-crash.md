# PostgreSQL server crash implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Verify actual PostgreSQL process death during two flushed, uncommitted synthetic business actions, followed by WAL recovery and idempotent application retries.

**Architecture:** Reuse the observed-transaction and domain assertions from the client-kill verifier through an optional crash callback. A separate wrapper creates and owns a disposable Docker container and volume; strict identity, label and mount checks guard destructive operations. The shared Compose database is never selected.

**Tech Stack:** Python, psycopg, SQLAlchemy, cached Docker PostgreSQL 17/pgvector image.

**Spec:** `plans/resolveai-v6.md` §§4, 7, 11; verified remaining server-failure gate in `docs/STATUS.md`.

## Global constraints

- Synthetic fixtures and simulated refunds only; real domain authorization, approval and idempotency checks remain active.
- No model or Cloud calls, credentials, shared database selection, or image download.
- Reports use local manifest, case JSONL, summary and HTML; locked release remains false.

## Review focus

- A wrong container ID, missing labels or different mounted volume must refuse kill/removal.
- A published non-loopback port must fail ownership validation before a destructive operation.
- A restart may reuse a PostgreSQL backend PID; disappearance checks must also match application name.
- Committed approval must survive while uncommitted ledger, audit, balance and version updates disappear.
- Setup/recovery failure must preserve diagnostics and owned volume; cleanup must remain guarded.

### Task 1: Owned server-crash verification

**Files:** Create `scripts/verify_postgres_server_crash.py`, `tests/test_server_crash_ownership.py`, and `docs/implementation/postgres-server-crash-2026-10-05.md`; modify `scripts/verify_transaction_kill_postgres.py`, README, STATUS and ISSUES.

**Interfaces:** `run_case(stage, admin_url, run_id, report_dir, crash_server=None)` invokes `crash_server(stage) -> dict[str, bool]` after observing the open transaction. `require_owned_container(info, container_id, run_id, volume)` and `require_owned_volume(info, volume, run_id)` raise on mismatches. `DisposablePostgres` creates, crashes/restarts and cleans up its own resources.

- [x] Write ownership regression tests for all identity/label/mount/port failures and a valid isolated fixture.
- [x] Run `.venv/bin/python -m pytest tests/test_server_crash_ownership.py -q`; expected failure because verifier is absent.
- [x] Implement guards, bounded Docker/connection operations, immutable cached-image startup, crash callback, WAL evidence and sanitized failure reports. Match backend PID plus application name after restart; dispose old engine connections before recovery.
- [x] Run ownership tests; expected all pass.
- [x] Run `.venv/bin/python scripts/verify_postgres_server_crash.py`; expected two successful real server SIGKILL cases, observed WAL recovery and all rollback/retry assertions passing. Retain failed reports.
- [x] Run original client-kill verifier; expected 2/2 and unchanged 29 business checks.
- [x] Run Python suite and deployed OIDC verifier; expected no failures and seven deployed checks passing.
- [x] Obtain one fresh read-only whole-slice review, fix any contract failure with a demonstrated red→green regression, and document actual evidence.
- [x] Commit explicit files after `git diff --check` passes.

## Ledger and rulings

- Self-review: this slice covers the server-process recovery gap only; hardware power loss, backup restoration, offline copies and locked human evaluation remain outside its claims.
- Ruling: Continue on the user's previously authorized local master and execute without approval pauses, following their explicit autonomous instruction — cost: shared-branch changes need normal Git review.
- Ruling: Cached image only and a loopback ephemeral port make the test reproducible without downloads or touching Compose — cost: Docker and that cached image must already exist.
- Ruling: Successful runs remove only verified owned resources; failed runs preserve their volume and diagnostics for review — cost: failures consume a small local volume until explicit cleanup.
- Ruling: Docker reassigns an unspecified host port on restart (observed `44622` then `44623`), so select a free OS port and configure that explicit loopback binding — cost: a competing bind between the probe and Docker startup makes setup fail safely rather than retrying against another server.
- Ruling: Upgrade the review's diagnostic minor to a required fix because the user expressly requested troubleshooting logs. Record only a known Docker failure category and exit code — cost: unusual failures still need local inspection rather than raw stderr export.

- Execution: server run `20261005T010202Z-serverkill-b4dfb6` 2/2,43/43; original client run `20261005T010307Z-txkill-f02c42` 2/2,29/29; ownership/diagnostics14/14; finalPython197passed/1opt-inPGskip; deployedOIDC7/7. Fresh review found only the required diagnostic follow-up, reproduced red→green; no deferred findings.
