# Docker Compose integration run — 2026-09-30

## Environment and commands

Docker Engine 29.8.2 and Compose 5.5.1 ran from Ubuntu WSL. The default mock stack was built with `docker compose -f infra/compose/compose.yaml up --build -d`. It started PostgreSQL, Redis, API, worker, Commerce MCP, web, OTel Collector, and Jaeger. PostgreSQL and API health checks passed; the web page returned HTTP 200 at `127.0.0.1:3000` and the API health endpoint returned HTTP 200 at `127.0.0.1:8000/health`.

The API applied Alembic head `9d24757b98e1` and seeded 25 synthetic orders. On PostgreSQL 17.11 with pgvector 0.8.6, `docker compose -f infra/compose/compose.yaml exec -T api python scripts/verify_postgres.py --expected-orders 25` passed its vector-distance check and all six database integrity checks. After the workflow, counts included one return, one proposal, one approval, and one refund ledger entry.

`docker compose -f infra/compose/compose.yaml exec -T api python scripts/demo_workflow.py` produced one approved simulated refund of 1,018 CNY cents. Its replay produced no additional ledger entry. The worker container also executed its polling loop and reported an empty pending batch after the refund. This is a synthetic ledger, with no payment provider call.

A `POST /chat` with `cust-01`, `demo-order-02`, and `agent_mode=collab` returned HTTP 200 with order and policy findings. The same request as `cust-02` returned HTTP 404 `order_not_found`. A two-turn replay returned two findings with revision 1 on the first turn and two findings with revision 2 on the second. The API container was recreated between chat checks, and its database-backed thread revision continued. Jaeger's `/api/traces/{trace_id}` returned seven spans under `resolveai-api`, including both specialist spans.

## Failures fixed during the run

The first chat request timed out because `PostgresSaver.setup()` attempted `CREATE INDEX CONCURRENTLY` while the request held an open PostgreSQL transaction. Checkpoint setup now runs during API startup, before requests. The first post-restart chat also exposed findings from an earlier turn through the LangGraph list reducer. Checkpoint keys now include plan revision; a regression test and the Docker replay passed. The initial OTel provider warning and unused metrics exporter 404 stopped after provider reuse and Compose telemetry configuration.

## Limits

The default mock stack does not require external keys. Commerce MCP requires a Keycloak JWT, and Keycloak sign-in was not configured or exercised. Langfuse Cloud and OpenRouter were not configured. Playwright browser journeys, concurrent workers, full stack restart recovery, and scheduled deadline alert timing were not exercised. The separate [PostgreSQL 16 report](postgres-integration-2026-09-30.md) covers the 100,000-order import; it was not repeated inside Docker.
