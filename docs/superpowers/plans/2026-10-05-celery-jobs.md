# Controlled Redis/Celery jobs implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy and verify isolated, bounded Celery refund/deadline/index jobs over Redis while retaining SQL authorization.

**Architecture:** Thin registered JSON tasks call existing worker/index domain functions, with configured namespace/database isolation. Compose uses separate prefork worker and Beat services; HTTP approval remains independent of broker delivery.

**Tech Stack:** Celery 5.6, Redis 7, PostgreSQL 17, SQLAlchemy, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-05-celery-jobs-design.md`; v6 §§2, 4, 9, 10.

## Global constraints

- No approval, monetary or database override in task messages; PostgreSQL remains authoritative.
- No external paid calls; isolated synthetic fixtures and generated resource ownership required.
- Explicit JSON serializers, disabled result backend, bounded retry/time/prefetch; no public broker port.
- Continue native on authorized local master; stage only explicit files and do not push automatically.

## Review focus

- Namespace mismatch or reused namespace for different databases must not cause cross-fixture writes.
- Duplicate or restarted jobs must never issue an unapproved or repeated refund.
- Broker/worker failures must not block HTTP approval or erase the SQL recovery source.
- Forked workers must not reuse live parent SQL connections; technical failure retries must stay bounded.
- Scheduler duplication/expired backlog and task arguments must preserve safe, repeatable business results.

### Task 1: Controlled task boundary and Docker deployment

**Files:** Create `apps/api/resolveai/jobs.py`, `tests/test_jobs.py`, `scripts/verify_celery_jobs.py`, implementation report; modify `pyproject.toml`, `uv.lock`, `.env.example`, Compose, README, STATUS and ISSUES.

**Interfaces:** `make_app(namespace, database_url, broker_url) -> Celery` validates config and isolates queues/Redis prefixes. Registered tasks accept only `namespace: str`. Refund/deadline tasks return processed counts; index task returns bundle/clause counts. `python -m resolveai.worker` remains direct. Verifier creates owned Redis/PostgreSQL and separate worker/Beat processes, saves JSON/HTML reports and safe logs.

- [x] Write and run failing real-domain task regressions for namespace rejection, unapproved/duplicate refund, deadline deduplication and separate-database queue isolation.
- [x] Install/lock Celery Redis dependencies, implement isolated JSON tasks with limits/retry/fork cleanup; run focused tests green.
- [x] Replace Compose polling command with worker, add Beat and Redis health/dependencies; preserve no-key startup.
- [x] Run isolated real broker/worker/Beat verification including duplicates, wrong namespace, indexing, worker restart and broker outage recovery; inspect terminal SQL checks and resource cleanup.
- [x] Run full Python suite, rebuild/start Docker services, verify worker readiness and seven OIDC/API/MCP checks.
- [ ] Obtain one fresh read-only review, fix Critical/Important findings in one red→green pass and run required verification again.
- [ ] Document actual outcomes and scope, run diff check, commit explicit files and record task completion.

## Ledger and rulings

- Ruling: User's autonomous v6 instruction supplies design/plan execution authorization; no approval pause. Cost: assumptions must remain reviewable in this written design.
- Ruling: Periodic SQL scans repair jobs rather than adding API transaction/outbox coupling. Cost: up to the configured polling interval for issuance when healthy.
- Ruling: Published-policy periodic indexing establishes safe queue use; bulk evaluation/data/memory tasks remain explicitly open because they require different resource and scope contracts.
