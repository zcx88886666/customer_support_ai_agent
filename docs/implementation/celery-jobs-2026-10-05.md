# Controlled Redis/Celery jobs, 2026-10-05

The [job boundary](../../apps/api/resolveai/jobs.py) registers three private JSON tasks: simulated-refund scans, receipt deadline alerts and published-policy index refresh. Each task requires the configured namespace and connects only to its configured database. Database identity contributes to both the queue name and Redis key prefix, so workers sharing a namespace for different databases use separate queues. Messages contain no database URL, customer identity, money or approval. PostgreSQL and the existing guarded domain functions remain authoritative.

Compose now starts a two-child Celery prefork worker and a separate Beat scheduler over private Redis. Refund/deadline scans run every 30 seconds; index refresh every five minutes. Periodic messages expire after 90 seconds; tasks have 45-second soft and 60-second hard limits, three technical SQL retries, late acknowledgement, worker-loss requeue and prefetch one. Task results are not stored. Each task creates and disposes its own engine in the executing child, avoiding inherited SQL connections. Worker and Beat restart unless explicitly stopped; Beat uses its own schedule volume. Direct `.venv/bin/python -m resolveai.worker` remains available without Redis.

HTTP approval does not publish a queue message or depend on Redis. An approved proposal remains in SQL until a healthy scheduled scan can issue it, after all current domain checks. Delivery may repeat; ledger uniqueness and order locking preserve idempotency.

## Actual verification

- Eight task tests first failed for the missing boundary and then passed with real synthetic domain data: an unapproved scan issues nothing, approval allows one ledger, repeated jobs add nothing, foreign namespace is rejected, overdue alerts deduplicate, invalid namespace rejects startup and separate databases have different queue/prefix identities.
- Isolated real Redis/PostgreSQL run `20261005T071921Z-celery-a81b49` passed **15/15 checks**. A two-child worker processed duplicate refund messages into exactly one ledger, rejected a foreign namespace, left a different-database message unconsumed, generated one due-soon and one overdue alert, and refreshed the current policy index. Restart/replay added no duplicate. While Redis was stopped, a second HTTP approval committed and remained authorized in SQL. The tmpfs Redis restart lost the queue; an ordinary 30-second Beat scan recovered that approval. Final SQL contained **two unique authorized ledgers, two issuance audits, matching amounts/quantities, two deadline alerts and two deadline audits**. All owned successful Redis/PostgreSQL containers and the PostgreSQL volume were removed.
- Initial run `20261005T071625Z-celery-9816f4` failed fixture setup because demo order 02 is undelivered. The domain correctly rejected it; the verifier now selects delivered order 06. Its stopped owned fixtures and logs remain ignored.
- Second run `20261005T071731Z-celery-371746` passed the first 11 checks, including approval during the broker outage, then the verifier's pre-outage Celery control client raised a connection-reset error. Recreating that client and using fresh bounded readiness connections corrected the probe. Its failed logs and stopped fixtures remain.
- Full Python suite passed **222 tests**, with three optional PostgreSQL tests skipped, in 22.07 seconds. Docker API/worker/Beat images built and services started; all seven deployed real-OIDC/API/MCP checks passed. The deployed worker returned one `pong` after startup; an earlier immediate ping ran before it was ready. Fresh review found two Important issues; the follow-up below records their fixes.

```bash
.venv/bin/python scripts/verify_celery_jobs.py
```

The verifier uses cached `redis:7-alpine` and `pgvector/pgvector:pg17` images, uniquely named labeled containers, loopback ports and isolated API/worker/Beat processes. It records script hashes, actual image/container IDs, job IDs, terminal checks and safe errors under ignored `evals/reports/<run_id>/`. Ownership is checked before stop/start/remove; failures preserve stopped fixtures. It spends no provider credit.

This establishes the existing operational job path. Bulk generation/evaluation job orchestration and optional memory extraction remain open; periodic policy refresh does not enable semantic retrieval or auto-publish drafts. The human-reviewed locked release gate remains false.

Configuration and broker behavior follow the [Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html), [Redis broker](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) and [task acknowledgement](https://docs.celeryq.dev/en/stable/userguide/tasks.html) documentation.

## Final review and fixes

The independent reviewer reproduced two defects. First, omitted PostgreSQL host/port/database values could resolve through libpq defaults to different targets while sharing a queue identity. Jobs now require an explicit host, port and database and reject service/target overrides; seven target regressions went red→green. Second, `KeyboardInterrupt` could remove fixtures after only a subset of checks and skip the report. The verifier now tracks full completion, records cancellation as incomplete and returns 130. Its regression went red→green.

Actual SIGINT probe `20261005T073128Z-celery-7eb01f` interrupted after one successful job: the report records one passing check, `probe_completed=false`, no fixture removal and a KeyboardInterrupt. Both owned servers were confirmed stopped; logs were copied from the test temporary directory into ignored `evals/reports/20261005T073128Z-celery-7eb01f/`. The optional Docker cancellation test passed **2/2** including the ordinary cancellation regression.

Final full recovery run `20261005T073124Z-celery-bcda5a` passed **16/16** checks with successful owned-resource removal. Final full suite passed **230 tests**, with four opt-in Docker/PostgreSQL skips, in 21.43 seconds. The task/cancellation set passed **16 tests with one Docker skip**; that Docker case passed separately. No Critical/Important findings remain after the single fix pass, and there were no deferred Minor findings. Broader bulk jobs, memory extraction, production payment/exactly-once claims and locked throughput gates were explicitly outside this slice.

After the final rebuild, the deployed worker returned one `pong` and all seven real-OIDC/API/MCP checks passed again.
