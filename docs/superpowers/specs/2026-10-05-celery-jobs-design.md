# Redis/Celery controlled jobs design

Implements v6 §2 asynchronous-job foundation for existing simulated-refund processing, deadline alerts and published-policy index refresh. User authorization is the instruction to finish v6 autonomously without further approval pauses. Existing domain functions remain the sole source of authorization and transaction/idempotency rules.

## Selected approach

Replace the Compose shell polling worker with a Celery prefork worker and separate single Beat scheduler. Redis carries JSON messages and stays private to the Compose network. Each worker is bound to one configured database; task messages carry only a required job namespace, never database URLs, role claims, approval or money. A validated namespace plus a database-derived queue/key prefix isolates development and evaluation workers. Namespace mismatches reject before accessing SQL. Periodic scans query approved SQL proposals rather than relying on an API enqueue succeeding after commit, so broker downtime cannot block an HTTP approval or lose its authoritative eligibility.

Keeping the shell loop leaves the queue path unimplemented. Enqueuing directly from API transactions would require an outbox and additional coupling. Periodic idempotent SQL scans use existing recovery semantics with the smallest interface change.

## Contract and bounds

`resolveai.jobs:app` registers `resolveai.jobs.refunds`, `resolveai.jobs.deadlines`, and `resolveai.jobs.policy_index`, each with a required `namespace` argument and count-only return data. Refund and deadline schedules run every 30 seconds; index refresh every 300 seconds. Messages expire after 90 seconds. Prefork jobs have a 45-second soft and 60-second hard limit, late acknowledgement, worker-loss requeue, prefetch one and bounded technical SQL retries. Result storage is disabled. Worker concurrency is two. Each task creates and disposes its SQL engine in the executing child, avoiding inherited connections. No HTTP or MCP route exposes these tasks.

Job namespace defaults to `dev`; invalid values fail startup. PostgreSQL jobs require an explicit host, port and database and reject service/target query overrides, avoiding libpq environment-dependent targets. Redis broker URL is configured separately. Redis key prefix and queue identity include a hash of database host/port/name, excluding credentials. Different database workers cannot accidentally consume one another's jobs. One Beat instance uses a persistent Compose volume. Direct no-key local one-shot workers remain runnable without Redis.

## Verification and limits

Unit/eager task checks use real synthetic domain data: no approval means no issuance; repeated tasks produce one ledger/audit/balance update; wrong namespace causes no writes; deadline repeats produce one alert/audit; unsupported policy states are not indexed. Real isolated Docker Redis/PostgreSQL workers prove queued duplicate/rejected jobs, scheduler delivery, index refresh, restart and broker outage recovery. Reports contain safe types/counts, hashes, owned identities and cleanup evidence. Only generated resources are removed; failed logs are preserved.

Tasks do not accept arbitrary subprocesses, customer identities or financial parameters. This slice does not claim distributed exactly-once delivery, real-model evaluation queues, optional memory extraction, semantic retrieval or bulk-data job orchestration. PostgreSQL idempotency makes repeated delivery safe; periodic scans repair missed jobs. Later bulk jobs require separately constrained interfaces.

References: [Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html), [Redis broker](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html), [task idempotency](https://docs.celeryq.dev/en/stable/userguide/tasks.html).
