# Sustained mixed write load implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Measure sustained mock-auth HTTP read/chat/return/warehouse/approval traffic with a concurrent refund worker and verify final database safety.

**Architecture:** An isolated migrated PostgreSQL Docker fixture receives synthetic single-item orders. A temporary local API and worker process use only that database and no model/Cloud keys. Cached k6 drives concurrent complete workflows for a fixed duration; a scorer compares HTTP action counters with distinct committed business records, exact money and required approvals.

**Tech Stack:** Python, FastAPI/Uvicorn, Docker PostgreSQL 17, cached k6 0.56.0, SQLAlchemy.

**Spec:** `plans/resolveai-v6.md` §§4, 9, 11; operational gaps in `docs/STATUS.md`.

## Global constraints

- Synthetic fixtures and simulated refunds only; no shared Compose database writes, OpenRouter or Langfuse calls.
- Temporary processes/containers/resources are owned and cleaned only after identity validation; failure evidence stays in ignored reports.
- Report actual duration, concurrency, throughput, P50/P95, errors, business terminal assertions and limitations without an SLA claim.

## Review focus

- No refund ledger without one committed approval and positive inspection.
- Every issued amount equals the paid allocation and refunded item totals, with no duplicate proposal/ledger.
- A failed HTTP stage must not count as a completed workflow.
- Different VUs/iterations must use different order and idempotency references; exhaustion must not wrap to an existing order.
- Worker/API failures and incomplete issuance must fail the report; cleanup must not touch other services.

### Task 1: Isolated sustained mixed-write probe

**Files:** Create `evals/load/mixed_write.js`, `evals/runners/run_mixed_write_load.py`, `tests/test_mixed_write_scoring.py`, `docs/implementation/mixed-write-load-2026-10-05.md`; update README/STATUS/ISSUES.

**Discovered defect:** Modify `apps/api/resolveai/approval_checkpoint.py` and add `tests/test_approval_concurrency.py` to prevent the real worker commit from making a valid approval checkpoint appear stale. Lock order before all status reads, matching the worker's locking order.

**Interfaces:** k6 receives `TARGET_URL`, `LOAD_ORDERS`, `LOAD_VUS`, `LOAD_DURATION`. Runner publishes `manifest.json`, `case_results.jsonl`, `summary.json`, `report.html` and raw k6/worker logs. `score_terminal(db, expected_completed)` returns checks/counts.

- [x] Write scorer failing regressions for missing approval/inspection, duplicate ledger and wrong money; run them red.
- [x] Implement fixture isolation, bounded workload driver, worker process, terminal scorer and report.
- [x] Run focused tests green and one preflight workflow against the owned stack.
- [x] Run at least a 60-second mixed workload with the cached k6 image; inspect HTTP and SQL assertions, latency and errors.
- [x] Run full Python suite and obtain one fresh read-only review. Fix Critical/Important issues with red→green evidence.
- [x] Document measured results, `git diff --check`, commit explicit files.

## Ledger and rulings

- Ruling: Continue native on previously authorized local master and use only disposable synthetic fixtures — cost: shared-branch review remains necessary.
- Ruling: One sustained 10-VU development profile suffices to establish an observed write-flow measurement; it cannot establish production capacity or a locked evaluation result.
- Ruling: The observed approval/worker race is fixed in this slice because it causes real HTTP failures; the synchronized PostgreSQL regression must go red→green. Checkpoint verification briefly holds the order lock until request transaction end.
- Ruling: Duplicate ledger insertion is constrained by the existing unique key and covered by domain idempotency tests; the load scorer additionally checks one ledger per proposal.

- Ruling: Reuse domain inspection and unique-ledger/idempotency regressions instead of constructing an impossible duplicate row; load checks additionally verify positive inspection and ledger uniqueness.
- Final review: two Important verifier findings fixed in one red→green pass; the exhaustion Minor was fixed as a sustained-traffic requirement. No deferred findings.
- Final run: `20261005T070118Z-mixed-write-db39b5`, 697 workflows, 4,879 requests, 13/13 checks; full suite 214 passed, three opt-in PostgreSQL skips; real concurrency/checkpoint/load set 8/8.
