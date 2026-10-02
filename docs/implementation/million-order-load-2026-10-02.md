# Million-order PostgreSQL import and API load — 2026-10-02

## Environment and data

Ubuntu 24.04 under WSL reported 20 logical CPUs and 15 GiB RAM. Docker Compose ran PostgreSQL 17.11 with pgvector 0.8.6. The isolated `resolveai_scale` database was migrated to `8a512e96af34`, seeded with 25 demo orders, then loaded from the fixed-seed `scale-1m-v3` synthetic CSVs. The live `resolveai` demo database was not used for load traffic. The CSV importer reran its independent validation before `COPY`; the import of 17 tables took **122.533 seconds**.

The database contained **1,000,025 orders**, 1,250,025 items, 5,965,564 shipment events, and 23,090 synthetic refund ledger rows after import. `scripts/verify_postgres.py --expected-orders 1000025` passed before and after load: all six ownership, allocation, refund-approval, and balance violation counts were zero. The full 895 MiB generated CSV directory remains ignored; it is not committed.

## No-key mixed workload

An isolated API container used the scale database, mock identity headers, and empty OpenRouter/Langfuse/OTel settings. A synthetic preflight returned HTTP 200 for health, owned order list/detail, shipment list, and a single order-status chat. k6 0.56.0 ran [the reproducible script](../../evals/load/million_orders.js) on the local Docker network. Each virtual user repeatedly sent about 40% order lists, 30% owned order details, 20% shipments, and 10% one-turn order-status chats, with a 0.3-second think time. Chats wrote only isolated thread/checkpoint data; the workload made no return or refund calls. Each fixed profile ran for 20 seconds; numbers below come from the committed k6 aggregate summaries.

| Concurrent users | Requests | Throughput (req/s) | Overall P50 / P95 | List P95 | Detail P95 | Shipment P95 | Chat P95 | HTTP errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20 | 1,203 | 54.58 | 4.22 / 358.58 ms | 9.03 ms | 9.44 ms | 31.74 ms | 548.01 ms | 0 |
| 50 | 2,471 | 107.91 | 5.89 / 635.13 ms | 215.83 ms | 15.19 ms | 444.45 ms | 1,393.39 ms | 0 |
| 100 | 3,129 | 131.23 | 35.34 / 2,100.76 ms | 509.91 ms | 76.45 ms | 4,117.11 ms | 4,994.02 ms | 0 |

A separate 95-second 20→50→100 staged run completed **11,491 requests**, **0 HTTP errors**, 111.25 requests/second overall, and a 480.11 ms overall P95. Aggregate machine-readable summaries: [20 users](million-orders-k6-20.json), [50 users](million-orders-k6-50.json), [100 users](million-orders-k6-100.json), and [staged run](million-orders-k6-summary.json). One-point Docker resource samples during load showed the API at about 29% CPU/145 MiB near 20 users, 122% CPU/179 MiB near 50, and 114% CPU/175 MiB near 100; the shared PostgreSQL container used about 1.49–1.56 GiB and 8–27% CPU at those sample times. These are snapshots, not averages.

The short local profile uses mock authentication, no external model, one API process, and loopback Docker networking. At 100 users the rate increased less than concurrency and chat/shipment P95 rose sharply; these measurements identify a performance issue to investigate, not a production capacity or SLA claim. OIDC, model-provider latency, return/approval write traffic, sustained load, and worker contention were not included. The scale database remains available for follow-up tests without recopying the CSVs.

## Reproduction on a fresh scale database

Use the generated `data/generated/scale-1m-v3` fixture from the documented generator command. The commands below assume the normal Compose stack is running and `resolveai_scale` does not already exist. They keep the API model and Cloud exporters disabled for the scale container.

```bash
docker compose --env-file .env -f infra/compose/compose.yaml exec -T postgres createdb -U resolveai resolveai_scale
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_scale api alembic upgrade head
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_scale api python -m resolveai.seed --clock 2026-09-29T12:00:00+00:00
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -v "$PWD/data/generator:/app/data/generator:ro" -v "$PWD/data/generated/scale-1m-v3:/app/data/generated/scale-1m-v3:rw" -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_scale api python data/generator/import_postgres.py data/generated/scale-1m-v3
docker compose --env-file .env -f infra/compose/compose.yaml run --rm --no-deps -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_scale api python scripts/verify_postgres.py --expected-orders 1000025
docker compose --env-file .env -f infra/compose/compose.yaml run -d --rm --no-deps --name resolveai-scale-api -p 127.0.0.1:8002:8000 -e DATABASE_URL=postgresql+psycopg://resolveai@postgres:5432/resolveai_scale -e AUTH_MODE=mock -e OPENROUTER_API_KEY= -e LANGFUSE_PUBLIC_KEY= -e LANGFUSE_SECRET_KEY= -e OTEL_EXPORTER_OTLP_ENDPOINT= api
```

After the scale API reports healthy, use `docker run --rm --network compose_default -v "$PWD/evals/load:/scripts:ro" -v /tmp/resolveai-load:/reports:rw -e TARGET_URL=http://resolveai-scale-api:8000 -e FIXED_VUS=20 -e FIXED_DURATION=20s grafana/k6:0.56.0 run --quiet --summary-export=/reports/million-orders-20.json /scripts/million_orders.js`, substituting 50 and 100 for the other profiles. Create a writable `/tmp/resolveai-load` directory first. Stop the temporary API with `docker stop resolveai-scale-api`; the database remains for later runs.
