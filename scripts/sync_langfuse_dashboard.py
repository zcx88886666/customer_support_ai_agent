"""Create and verify the versioned Langfuse dashboard without overwriting Cloud edits."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "observability/dashboards/resolveai-overview-v1.json"
BASE_PATH = "/api/public/unstable"


def load_local_env() -> None:
    """Use the ignored local file for the CLI without echoing secrets."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"}:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class LangfuseDashboardApi:
    def __init__(self) -> None:
        load_local_env()
        public = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret = os.getenv("LANGFUSE_SECRET_KEY")
        if not public or not secret:
            raise RuntimeError("Langfuse project keys are not configured")
        base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")
        self.client = httpx.Client(base_url=base_url, auth=(public, secret), timeout=30)

    def request(self, method: str, path: str, **kwargs) -> dict:
        response = self.client.request(method, BASE_PATH + path, **kwargs)
        if response.status_code >= 400:
            # Cloud errors can contain submitted data; never echo response bodies.
            raise RuntimeError(f"Langfuse {method} {path} returned HTTP {response.status_code}")
        return response.json()

    def list_all(self, path: str) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, 101):
            result = self.request("GET", path, params={"page": page, "limit": 100})
            rows.extend(result["data"])
            if page >= result["meta"]["totalPages"]:
                return rows
        raise RuntimeError(f"Langfuse {path} exceeded 100 pages")


def exact_one(rows: list[dict], name: str, kind: str) -> dict | None:
    matches = [row for row in rows if row["name"] == name]
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate {kind} name: {name}")
    return matches[0] if matches else None


def matching_widget(remote: dict, spec: dict) -> bool:
    keys = ("name", "description", "view", "dimensions", "metrics", "filters", "chartType")
    return all(remote.get(key) == spec.get(key) for key in keys)


def sync(apply: bool) -> dict:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    names = [widget["name"] for widget in spec["widgets"]]
    if len(names) != len(set(names)):
        raise RuntimeError("Dashboard specification contains duplicate widget names")
    api = LangfuseDashboardApi()
    dashboards = api.list_all("/dashboards")
    widgets = api.list_all("/dashboard-widgets")
    dashboard = exact_one(dashboards, spec["name"], "dashboard")
    if dashboard and (dashboard.get("description") != spec["description"] or dashboard.get("filters") != []):
        raise RuntimeError("Dashboard description or filters drifted; refusing overwrite")
    created: list[str] = []
    resolved: list[dict] = []
    for wanted in spec["widgets"]:
        widget = exact_one(widgets, wanted["name"], "widget")
        if widget and not matching_widget(widget, wanted):
            raise RuntimeError(f"Widget drifted; refusing overwrite: {wanted['name']}")
        if widget is None and apply:
            widget = api.request("POST", "/dashboard-widgets", json=wanted)
            created.append(wanted["name"])
        if widget is not None:
            resolved.append(widget)
    if dashboard is None and apply:
        dashboard = api.request("POST", "/dashboards", json={"name": spec["name"], "description": spec["description"], "filters": []})
        created.append(spec["name"])
    if dashboard is None:
        return {"status": "pending", "dashboard": spec["name"], "missing_widgets": [name for name in names if not any(row["name"] == name for row in resolved)]}
    dashboard = api.request("GET", "/dashboards/" + dashboard["id"])
    placements = dashboard["definition"]["widgets"]
    for index, widget in enumerate(resolved):
        matches = [placement for placement in placements if placement["type"] == "widget" and placement["widgetId"] == widget["id"]]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate placement: {widget['name']}")
        if not matches and apply:
            placement = {"type": "widget", "widgetId": widget["id"], "x": (index % 2) * 6, "y": (index // 2) * 4, "width": 6, "height": 4}
            api.request("POST", "/dashboards/" + dashboard["id"] + "/placements", json=placement)
            created.append("placement:" + widget["name"])
    dashboard = api.request("GET", "/dashboards/" + dashboard["id"])
    placed_ids = {row["widgetId"] for row in dashboard["definition"]["widgets"] if row["type"] == "widget"}
    missing = [name for name in names if not any(widget["name"] == name and widget["id"] in placed_ids for widget in resolved)]
    return {"status": "ok" if not missing else "pending", "dashboard": spec["name"], "dashboard_id": dashboard["id"], "widgets": len(resolved), "placements": len(placed_ids), "created": created, "missing_placements": missing}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create missing Cloud resources")
    args = parser.parse_args()
    try:
        result = sync(args.apply)
    except Exception as exc:
        result = {"status": "incomplete", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "ok" or result["status"] == "pending" and not args.apply else 1)
