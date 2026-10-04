# Langfuse custom dashboard and usage calibration — 2026-10-03

The configured US project at `https://us.cloud.langfuse.com` initially listed zero custom dashboards and widgets. The repository now defines `observability/dashboards/resolveai-overview-v1.json` and a repeatable API sync. It created `ResolveAI development overview v1` (`cmut6wsa007ggad0c5zk77cm2`) with four number widgets: uploaded root observations, uploaded generation cost, uploaded HTTP P95 latency, and uploaded `task_success` average. Each metric query returned HTTP 200 before creation. A readback found all four placements; a second `--apply` returned `created: []`. The script fails on changed widget definitions or dashboard filters instead of overwriting edits.

The dashboard describes only uploaded and sampled Cloud data. It does not determine release gates or count all local evaluation cases. The [local reports](../../evals/) remain authoritative. The dashboard API is currently marked unstable in [Langfuse's documentation](https://langfuse.com/docs/metrics/features/custom-dashboards), so the sync intentionally uses a versioned definition and validates readback.

For 2026-10-01 00:00 UTC through 2026-10-04 02:18 UTC, `scripts/measure_langfuse_usage.py --month 2026-10 --budget 50000` returned 5,221 observations, 4,559 semantic root observations, 29 numeric/boolean scores, and zero categorical scores. Their sum is a **9,809-unit proxy**, or 19.62% of an *assumed* 50,000-unit monthly Hobby allowance. [Langfuse defines units](https://langfuse.com/docs/administration/billable-units) as traces plus observations plus scores, but Metrics API v2 has no traces view; a semantic root is not necessarily an exact trace count. The project tier and authoritative billable usage were not available through this public API check. Confirm those in the built-in **Langfuse Usage Management** dashboard. The API queries expose only aggregates; no credentials or raw traces were written to this report.

Commands:

```bash
.venv/bin/python scripts/sync_langfuse_dashboard.py
.venv/bin/python scripts/sync_langfuse_dashboard.py --apply
.venv/bin/python scripts/measure_langfuse_usage.py --month 2026-10 --budget 50000
```
