# Mixed HTTP write load and approval recovery, 2026-10-05

The [runner](../../evals/runners/run_mixed_write_load.py) creates an owned Docker PostgreSQL 17 fixture, migrates and seeds 1,000 eligible synthetic orders, and starts isolated API and refund-worker processes. Cached k6 drives ten concurrent users for 60 seconds through order detail, chat, confirmed return, warehouse receipt, positive inspection, proposal and supervisor approval. Each iteration uses its own order and idempotency key. No OpenRouter or Cloud calls are made.

## Measured result

Final run `20261005T070118Z-mixed-write-db39b5` passed **13/13 checks**, with **697 complete workflows**, **4,879 HTTP requests**, **zero HTTP errors**, and no exhausted order allocations. PostgreSQL contained exactly 697 returns, receipts, inspections, proposals, approvals and ledgers. The scorer checks approval before issuance, positive inspection, return/item/order/customer ownership, original paid allocation, one ledger per proposal, and every item's refunded balance and quantity.

| Measurement | Observed |
|---|---:|
| Configured concurrency and duration | 10 users, 60 seconds |
| Elapsed load command, including grace | 62.645 seconds |
| k6 reported HTTP throughput | 72.22 requests/second |
| HTTP P50 / P95 | 53.22 / 330.34 ms |
| Workflow P95 through approval | 859.60 ms |
| Successful reads / writes | 697 / 4,182 |

The WSL2 host exposed 20 logical CPUs and 16,255,652 KiB RAM. PostgreSQL was restricted to one CPU and 384 MiB; five Docker samples recorded 40.83–46.71% CPU and 87.51–125.7 MiB memory. The API ran as one local Uvicorn process, and the worker polled every 0.5 seconds. The successful fixture and volume were removed. Ignored reports retain the manifest, hashes, resource samples, HTTP metrics, terminal checks and process logs.

These are development measurements on small single-item fixtures, mock authentication and deterministic chat. Workflow latency ends at approval; asynchronous refund completion is checked afterward. They do not measure real-model latency, OIDC overhead, million-order write capacity or a production SLA.

## Failures and fixes

- Initial preflight `20261005T064211Z-mixed-write-99a06f` failed a paid-allocation foreign key because generated parents had not flushed. The fixture regression went red→green after explicit parent/child flush boundaries. Its stopped owned PostgreSQL fixture remains for inspection.
- First sustained run `20261005T064404Z-mixed-write-f2f91e` issued 5,033 HTTP requests with 16 approval-resume HTTP 503 responses after SQL approval had committed. The checkpoint read combined an approved proposal from before a worker commit with the order version afterward. A synchronized real PostgreSQL regression reproduced a false `stale` result. Checkpoint verification now locks the order before reading those facts, using the same lock as issuance; the regression passed after the fix. Failed logs and the stopped fixture remain. The cached k6 legacy Rate field also required parsing `value` rather than `rate`.
- The first corrected run `20261005T065129Z-mixed-write-6f2824` passed 712 workflows and 4,984 requests with zero HTTP errors. A fresh reviewer then found two verifier gaps: k6 cleanup exceptions could prevent PostgreSQL cleanup/reporting, and swapping equally priced ledger item references could pass the scorer. Both regressions went red→green. The review's exhaustion concern was also addressed with an explicit counter and failing gate. The final run above exercised all three changes.

Final verification: **214 Python tests passed, three optional PostgreSQL tests skipped** in 21.76 seconds. The approval race, checkpoint and load checks passed **8/8** separately with the real PostgreSQL concurrency database enabled. The Docker API was rebuilt; all seven deployed OIDC/API/MCP checks passed, and isolated real-OIDC approved-refund replay `20261005T070416Z-oidc-b5eecd` passed with zero incomplete cases. No Critical/Important review findings remain after the single fix pass.

```bash
.venv/bin/python evals/runners/run_mixed_write_load.py
APPROVAL_CONCURRENCY_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres>:5432/postgres \
  .venv/bin/python -m pytest tests/test_approval_concurrency.py -q
```

Docker and cached `pgvector/pgvector:pg17` and `grafana/k6:0.56.0` images are required. The driver records immutable image IDs and validates ownership before cleanup. The locked release gate remains false. Wider write profiles, Redis/Celery delivery and backup/export retention remain separate work.
