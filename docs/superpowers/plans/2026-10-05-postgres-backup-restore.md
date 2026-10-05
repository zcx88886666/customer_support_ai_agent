# PostgreSQL local backup restore implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Produce and restore local synthetic PostgreSQL backups into a distinct server, preserving business records and parent graph checkpoints.

**Architecture:** Reuse guarded disposable PostgreSQL instances and verified client-crash fixtures. Create restricted custom-format dumps, restore into a new owned server, compare all public-table row hashes and schema/sequence metadata, then recheck checkpoint loading and domain idempotency. Keep archives and diagnostics locally; remove only verified successful Docker resources.

**Tech Stack:** Python, Docker PostgreSQL 17, pg_dump/pg_restore, psycopg, LangGraph PostgresSaver.

**Spec:** `plans/resolveai-v6.md` §§7, 11–12; remaining local backup/restore proof in `docs/STATUS.md`.

## Global constraints

- Synthetic data and simulated refunds only; no model/Cloud calls or shared Compose database selection.
- Use cached immutable PostgreSQL image and guarded run-specific volumes/containers.
- Archives and raw reports remain Git-ignored; local restoration does not imply offline media or retention-policy approval.

## Review focus

- Row counts can match after a ledger value is corrupted: compare canonical full-row hashes.
- A missing or extra checkpoint table must fail equality; fresh PostgresSaver must read the restored state.
- Constraints, indexes, column defaults, extensions and sequence state must survive restore.
- Restored domain replay must preserve a single return/refund and audit, without extra writes.
- A failed dump/restore must retain safe diagnostics and owned resources; file mode must be 0600.

### Task 1: Local PostgreSQL backup/restore proof

**Files:** Create `scripts/verify_postgres_backup_restore.py`, `tests/test_backup_restore_scoring.py`, `docs/implementation/postgres-backup-restore-2026-10-05.md`; update README, STATUS and ISSUES.

**Interfaces:** `snapshot_database(url) -> dict` records tables/counts/hashes, columns, constraints, indexes, extensions and sequence state. `restore_checks(before, after) -> dict[str, bool]` rejects changed contents or metadata. The script's main owns separate source/target `DisposablePostgres` instances and publishes local manifest/JSONL/summary/HTML.

- [x] Write scorer regressions: equal snapshots pass; equal row counts with changed ledger hash fail; missing/extra checkpoint tables and schema/sequence changes fail.
- [x] Run focused tests; expected missing verifier failure.
- [x] Implement snapshot/scorer, safe Docker stream commands, private archives, two source fixtures, real checkpoints, restore and idempotent replay.
- [x] Run focused tests; expected all pass.
- [x] Run `.venv/bin/python scripts/verify_postgres_backup_restore.py`; expected two successful independent-server restores and all data/schema/checkpoint/idempotency checks passing.
- [x] Run full Python suite; expected no failures.
- [x] Obtain one fresh read-only whole-slice review and fix any contract finding with red→green evidence.
- [x] Document actual outcomes, run `git diff --check`, and commit explicit files.

## Ledger and rulings

- Self-review: verifies intact logical local archives and new-server recovery; damaged storage, offline copy and erasure/retention policy remain open.
- Ruling: Continue native execution on authorized local master without approval pauses — cost: normal Git review remains necessary.
- Ruling: Reuse the crash verifier's guarded fixture utility; distinguish source/target by separate run labels — cost: this drill requires the cached Docker image.
- Ruling: Preserve private archives and failed owned resources under ignored report paths for user review — cost: a small amount of local disk until explicit cleanup.
- Ruling: Compare each sequence's `is_called` alongside `last_value`; equal last values can yield different next IDs — cost: one catalog read per sequence in this small drill.
- Ruling: The fresh review's column length and JSON precision findings are Important. Capture PostgreSQL `format_type` and column modifiers and serialize parsed `Decimal` tokens without converting them to floats — cost: comparison is intentionally strict about numerically equivalent but lexically different JSON encodings.

- Execution: final real run `20261005T055618Z-backup-restore-7fed2e` passed 2/2,65/65; ownership cleanup succeeded. Fresh review found two Important comparator false passes; both regressions were reproduced and fixed in one pass. Focused tests 13/13 with isolated real PostgreSQL. Final full Python suite 209 passed/2 opt-in PostgreSQL skipped. No deferred review findings.
