# Bounded deadline job batches implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure periodic deadline jobs make progress across large existing receipt histories within their execution allowance.

**Architecture:** SQL selects only unissued returns whose due-soon/overdue alert is currently missing. Queued jobs process at most100 candidates, and subsequent jobs select the remaining candidates; direct one-shot calls retain their existing full-scan-of-eligible behavior. Row locks and the composite alert key retain concurrency safety.

**Tech Stack:** SQLAlchemy, PostgreSQL/SQLite, existing Redis/Celery jobs.

**Spec:** v6 §§2,4,8,10 and existing `docs/superpowers/specs/2026-10-05-celery-jobs-design.md`. User authorized autonomous full implementation; this is a bounded refinement of the existing deadline worker.

## Constraints and review focus

- PostgreSQL remains authoritative; no arbitrary deadline/time arguments in queued messages.
- Already alerted or issued rows must not consume a pending batch and starve later receipts.
- Due-soon/overdue boundaries and transitions retain existing behavior; alerts/audits remain unique.
- Concurrent queued jobs and failed transactions may repeat safely; batch limits do not authorize money.
- Continue native on authorized local master; no external calls, no automatic push.

### Task1: Pending SQL selection and queued batch bound

**Files:** Modify `apps/api/resolveai/worker.py`, `apps/api/resolveai/jobs.py`, `scripts/verify_celery_jobs.py`; create `tests/test_deadline_batches.py` and implementation report; update README/STATUS/ISSUES.

**Interface:** `alert_refund_deadlines_once(at=None, *, session_factory=None, batch_size=None)` processes eligible pending rows, with optional validated1–1000 batch size; Celery supplies100. Existing direct callers remain compatible.

- [x] Write failing real-domain regression: two-row batches progress through seven overdue receipts and then cause no per-return SQL reads; due-soon alert does not suppress later overdue alert.
- [x] Implement correlated SQL candidate selection, deterministic ordering and optional limit; run focused tests green.
- [x] Run full Python suite and real isolated Redis/PostgreSQL queued-job probe; rebuild Docker worker/API/Beat and verify readiness/OIDC.
- [ ] Obtain one fresh read-only review; fix Critical/Important findings in one red→green pass.
- [ ] Record measured evidence, diff check and explicit local commit.

## Rulings

- Ruling: Filter eligible pending alerts in SQL instead of applying a limit to all receipts, which could repeatedly select old completed rows and prevent progress.
- Ruling: Keep the direct operator call unlimited over pending candidates; bound only scheduled jobs to100. Cost: huge direct scans remain the operator's responsibility.
- Ruling: Refund scanning is unchanged because naive batching would starve valid proposals behind unresolved invalid ones. Broader fair refund scheduling needs a separate contract.

- Ruling: Extend the real broker probe with205 historical overdue receipts to verify100/100/5/0 progress; its prior retroactive receipt fixture violated chronology, so an independently failing fixture test now pins correct delivery→return→receipt dates.
