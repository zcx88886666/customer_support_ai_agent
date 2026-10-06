import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.verify_langfuse_essential import hierarchy_result, load_local_langfuse_env
from scripts import export_langfuse_run


def test_local_env_loader_reads_only_langfuse_keys_without_execution(monkeypatch, tmp_path):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / ".env"
    config.write_text("LANGFUSE_PUBLIC_KEY='synthetic-public'\nLANGFUSE_SECRET_KEY=synthetic-secret\nLANGFUSE_BASE_URL=https://us.cloud.langfuse.com\nOPENROUTER_API_KEY=should-not-load\n")
    load_local_langfuse_env(config)
    import os
    assert os.getenv("LANGFUSE_PUBLIC_KEY") == "synthetic-public"
    assert os.getenv("LANGFUSE_SECRET_KEY") == "synthetic-secret"
    assert os.getenv("LANGFUSE_BASE_URL") == "https://us.cloud.langfuse.com"
    assert not os.getenv("OPENROUTER_API_KEY")


def test_hierarchy_requires_complete_essential_tree_and_no_missing_parent(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    path = traces / "synthetic.json"
    path.write_text(json.dumps({"observations": [
        {"id": "root", "name": "POST /chat", "parent_observation_id": None},
        {"id": "request", "name": "http.request", "parent_observation_id": "root"},
        {"id": "endpoint", "name": "fastapi.endpoint", "parent_observation_id": "request"},
        {"id": "resources", "name": "agent.request_resources", "parent_observation_id": "endpoint"},
        {"id": "policy", "name": "specialist.policy", "parent_observation_id": "resources"},
        {"id": "order", "name": "specialist.order", "parent_observation_id": "resources"},
    ]}))
    assert hierarchy_result(tmp_path, 1) == {"trace_files": 1, "connected": True, "problems": []}
    broken = json.loads(path.read_text())
    broken["observations"][4]["parent_observation_id"] = "filtered-parent"
    path.write_text(json.dumps(broken))
    assert hierarchy_result(tmp_path, 1)["problems"] == ["synthetic:missing_parent"]
    path.write_text(json.dumps({"observations": broken["observations"][:1]}))
    assert "synthetic:missing_essential_spans" in hierarchy_result(tmp_path, 1)["problems"]


def test_cloud_export_refreshes_early_cached_snapshot(monkeypatch, tmp_path):
    run_id = "synthetic-run"
    trace_id = "synthetic-trace"
    local = tmp_path / "evals/reports" / run_id
    local.mkdir(parents=True)
    (local / "case_results.jsonl").write_text(json.dumps({"case_id": "smoke-03", "agent_mode": "single", "status": "pass", "trace_id": trace_id}) + "\n")
    traces = tmp_path / "observability/exports" / run_id / "traces"
    traces.mkdir(parents=True)
    cached = traces / f"{trace_id}.json"
    cached.write_text(json.dumps({"trace_id": trace_id, "observations": [{"id": "root", "name": "POST /chat", "parent_observation_id": None}], "scores": [{"id": "score", "name": "task_success", "value": 1.0}]}))
    now = datetime.now(timezone.utc)
    observations = [SimpleNamespace(id=name, trace_id=trace_id, parent_observation_id=None if name == "root" else "root", name="POST /chat" if name == "root" else name, type="SPAN", start_time=now, metadata={"attributes.run_id": run_id, "attributes.case_id": "smoke-03"}) for name in ("root", "http.request", "fastapi.endpoint", "agent.request_resources", "specialist.policy", "specialist.order")]
    page = lambda data: SimpleNamespace(data=data, meta=SimpleNamespace(cursor=None))
    api = SimpleNamespace(observations=SimpleNamespace(get_many=lambda **kwargs: page(observations)), scores_v3=SimpleNamespace(get_many_v3=lambda **kwargs: page([SimpleNamespace(id="score", name="task_success", value=1.0, data_type="NUMERIC")])))
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=lambda **kwargs: SimpleNamespace(api=api)))
    monkeypatch.setattr(export_langfuse_run, "ROOT", tmp_path)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "synthetic-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "synthetic-secret")
    assert export_langfuse_run.export_run(run_id)["observations"] == 1
    assert export_langfuse_run.export_run(run_id, refresh=True)["observations"] == 6


def test_cloud_pagination_stops_at_deadline_after_rate_limit(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(export_langfuse_run.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export_langfuse_run.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class RateLimit(Exception):
        status_code = 429

    def fetch(_cursor):
        raise RateLimit()

    import pytest
    with pytest.raises(TimeoutError):
        list(export_langfuse_run.pages(fetch, deadline=3.0))
    assert clock[0] == 3.0
