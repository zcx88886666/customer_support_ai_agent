"""Measure a UTC calendar month's aggregate Langfuse units via Metrics API v2."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from sync_langfuse_dashboard import LangfuseDashboardApi


def count(api: LangfuseDashboardApi, view: str, start: str, end: str, *, roots: bool = False) -> int:
    query = {
        "view": view,
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "filters": [{"column": "isRootObservation", "operator": "=", "type": "boolean", "value": True}] if roots else [],
        "fromTimestamp": start,
        "toTimestamp": end,
    }
    response = api.client.get("/api/public/v2/metrics", params={"query": json.dumps(query)})
    if response.status_code >= 400:
        raise RuntimeError(f"Langfuse Metrics {view} returned HTTP {response.status_code}")
    rows = response.json()["data"]
    return int(rows[0]["count_count"]) if rows else 0


def measure(month: str, budget: int | None = None) -> dict:
    start_time = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if start_time > now:
        raise ValueError("Month must not be in the future")
    next_month = (start_time.replace(day=28) + timedelta(days=4)).replace(day=1)
    end_time = min(next_month, now)
    start, end = start_time.isoformat(), end_time.isoformat()
    api = LangfuseDashboardApi()
    observations = count(api, "observations", start, end)
    root_observations = count(api, "observations", start, end, roots=True)
    numeric_scores = count(api, "scores-numeric", start, end)
    categorical_scores = count(api, "scores-categorical", start, end)
    # The public Metrics API has no traces view. Semantic roots are only a proxy.
    proxy_units = observations + root_observations + numeric_scores + categorical_scores
    result = {
        "month_utc": month,
        "from": start,
        "to": end,
        "observations": observations,
        "semantic_root_observations": root_observations,
        "numeric_and_boolean_scores": numeric_scores,
        "categorical_scores": categorical_scores,
        "proxy_units": proxy_units,
        "note": "Root observations are a proxy for traces; confirm actual billable units in Langfuse Usage Management.",
    }
    if budget is not None:
        result["assumed_monthly_budget"] = budget
        result["proxy_fraction_of_budget"] = round(proxy_units / budget, 4)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"), help="UTC month, YYYY-MM")
    parser.add_argument("--budget", type=int, help="Optional assumed project monthly unit budget")
    args = parser.parse_args()
    try:
        if args.budget is not None and args.budget <= 0:
            raise ValueError("Budget must be positive")
        result = measure(args.month, args.budget)
    except Exception as exc:
        result = {"status": "incomplete", "error": str(exc)}
    print(json.dumps(result))
    raise SystemExit(1 if result.get("status") == "incomplete" else 0)
