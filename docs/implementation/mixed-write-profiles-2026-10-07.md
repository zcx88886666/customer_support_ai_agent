# Bounded sustained mixed-write profiles, 2026-10-07

The [mixed-write runner](../../evals/runners/run_mixed_write_load.py) now accepts bounded environment values before it creates a report directory or Docker fixture: `LOAD_VUS` from 1 to 100 (default 10), `LOAD_DURATION_SECONDS` from 1 to 300 (default 60), and `LOAD_ORDERS` from the user count to 10,000 (default 1,000). It records the requested profile in its manifest, including runs that fail after fixture allocation; invalid profiles fail before a report is made. The workload and 13 terminal database/audit checks are described in the [initial report](mixed-write-load-2026-10-05.md).

## Measured isolated runs

Each command used cached Docker PostgreSQL 17 and k6 images, one local API process, a local refund worker, mock authentication, deterministic chat, synthetic single-item orders, and no OpenRouter or Langfuse calls. Each run used a fresh migrated PostgreSQL fixture and 60 seconds of k6 traffic. The script checked all completed workflows against the committed database, then removed its owned container and volume.

| Users / prepared orders | Local report ID | Complete workflows | HTTP requests / errors | HTTP P95 | Workflow P95 through approval | HTTP requests/s | Terminal checks |
|---|---|---:|---:|---:|---:|---:|---:|
| 20 / 2,500 | `20261007T104848Z-mixed-write-91a0bc` | 624 | 4,368 / 0 | 776.59 ms | 3,216.35 ms | 67.50 | 13/13 |
| 50 / 5,000 | `20261007T105045Z-mixed-write-66cb3b` | 625 | 4,375 / 0 | 1,313.10 ms | 6,794.40 ms | 66.21 | 13/13 |
| 100 / 10,000 | `20261007T105226Z-mixed-write-d71093` | 659 | 4,613 / 0 | 2,834.65 ms | 12,058.30 ms | 65.45 | 13/13 |

PostgreSQL counts for returns, receipts, inspections, proposals, approvals and ledgers each equaled the completed-workflow count in every run. All three summaries recorded `cleanup.removed=true`, zero failed checks and `locked_release_ready=false`. The HTTP rate stayed near 65–67 requests/s as concurrent users increased, while latency rose; this suggests saturation in this local configuration, not a production capacity limit. Workflow P95 ends at approval; asynchronous refund completion is checked later. These runs do not measure real-model response time, OIDC overhead, large-order write capacity or a service-level target.

Reproduce a profile with, for example:

```bash
LOAD_VUS=20 LOAD_DURATION_SECONDS=60 LOAD_ORDERS=2500 \
  .venv/bin/python evals/runners/run_mixed_write_load.py
```

Focused profile and cleanup tests passed **9/9**. The final full Python suite passed **321 tests with five optional skips**; no-key minimum passed **7/7** (`20261007T105523Z-minimum-d5becd`). An independent read-only code review found no remaining Critical or Important issue after an orders-below-users preflight check was added. Reports under `evals/reports/` are ignored local artifacts and contain the manifests, raw k6 summaries, resource samples and process logs. The locked release gate remains false pending independent gold review.
