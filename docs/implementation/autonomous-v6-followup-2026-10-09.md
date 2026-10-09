# Autonomous v6 follow-up: bounded jobs and review preparation

> Verified on 2026-10-09 UTC against the local synthetic development stack. This report is engineering evidence, not a locked release result.

## Implemented

- Refund jobs now scan at most 100 approved proposal candidates per Celery task in Compose, then enqueue a cursor continuation when a full batch may have more. This lets the scan pass stale approved proposals without repeatedly starving later candidates. The domain service still makes every issuance decision and keeps the approval, ledger and idempotency gates.
- An operator can enqueue one of the fixed `demo`, `realistic` or `scale` synthetic generation profiles. Generation stages under the ignored data root, runs the independent quality validator, and publishes only validated output. Exact retries revalidate and reuse the output; changed contracts and incomplete outputs fail.
- A separate single-child bulk worker and broker queue keep generation and no-key evaluation work away from the scheduled refund, deadline and policy-index queue. The six fixed development evaluation suites use an isolated temporary SQLite URL, cleared provider and Langfuse credentials, bounded runtime, and a local task report. They do not set the locked release gate.
- Review preparation can validate completed forms and seek a leakage-safe grouped dev/locked **candidate** split. It refuses the current packet because each suite has only one connected fixture/template family. A candidate still requires human verification and a frozen locked replay.
- The [operations runbook](../OPERATIONS_RUNBOOK.md) and [threat model](../SECURITY_THREAT_MODEL.md) record the local operating and security boundaries.

## Verification

| Check | Observed result |
|---|---|
| Full no-key minimum, `PYTHONPATH=apps/api:. .venv/bin/python scripts/verify_minimum.py` | **7/7** development checks; Python **421 passed, 5 skipped**. Run `20261009T153528Z-minimum-02d90c`; locked gate remains false. |
| Focused jobs/review tests | **49 passed** across `tests/test_evaluation_jobs.py`, `tests/test_bulk_jobs.py`, `tests/test_jobs.py`, and `tests/test_grouped_split_candidate.py`. |
| Isolated Redis/PostgreSQL Celery verifier | **18/18** checks in run `20261009T152438Z-celery-eeb797`; owned test resources removed. This predates the separate broker queue change, which was checked through live Compose worker routing. |
| Docker Compose | Worker images built and both workers recreated; logs showed distinct operational and bulk queues/exchanges. `docker compose config --quiet` passed. |
| Live queued generation | `demo-bulk-isolated-20261009` completed **25/25** orders, zero quality violations and matching file hashes on the bulk worker. Operational refund/deadline scans continued separately. |
| Live queued evaluation | `policy_rag` and `core_business` passed in the bulk worker with **zero provider calls**. The later core result recorded a repository-relative child report path. |
| Review packet | Local packet `evals/review_packets/20261009T152716Z` validated as pending: **166 cases, 73 critical, 92 second-review assignments, zero completed human reviews, zero locked cases**. All suites have one connected group, so a safe grouped split was refused. |

Generated data, task logs and review forms remain in ignored local directories. No OpenRouter model or Langfuse export was used for this follow-up.

## Remaining boundary

These checks make the development stack easier to operate and verify; they do not establish an independent evaluation baseline. More independent case families, two human review streams where required, adjudication, source and version freeze, and a locked replay remain necessary. The local packet and exact next steps are in the [human handoff](../HUMAN_REVIEW_HANDOFF.md).
