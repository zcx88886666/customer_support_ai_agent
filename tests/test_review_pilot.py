"""New authored case families must enter the packet with their own provenance."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from scripts.prepare_minimum_review import group_component_counts, load_packet_cases
from evals.runners import run_review_pilot
from evals.runners.run_review_pilot import run_pilot


def test_review_packet_includes_two_new_world_families_in_every_suite():
    cases, sources = load_packet_cases()
    pilot = [case for case in cases if case["case_id"].startswith("review-")]
    assert len(pilot) == 14
    assert Counter(case["suite"] for case in pilot) == {
        "smoke_demo": 2, "core_business": 2, "intent_route": 2,
        "intent_dialogue": 2, "policy_rag_positive": 2,
        "policy_rag_negative": 2, "collaboration": 2}
    assert {case["group_keys"]["source"] for case in pilot} == {
        "review-world-harbor-v1", "review-world-ridge-v1"}
    assert all(count >= 2 for count in group_component_counts(cases).values())
    assert len([source for source in sources if "review_pilot_" in source["path"]]) == 7


def test_all_new_cases_replay_without_provider_or_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-should-be-cleared")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "fake-should-be-cleared")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "fake-should-be-cleared")
    summary = run_pilot(tmp_path)
    assert summary["cases"] == 14
    assert summary["counts"] == {"pass": 14}
    assert summary["provider_calls"] == 0
    assert summary["release_gate_pass"] is False
    assert json.loads((tmp_path / summary["run_id"] / "summary.json").read_text()) == summary


def test_pilot_loader_rejects_a_source_with_the_wrong_suite(tmp_path, monkeypatch):
    source = run_review_pilot.load_cases()[0][1]
    wrong = {**source, "suite": "unrelated_suite"}
    path = tmp_path / "wrong.jsonl"
    path.write_text(json.dumps(wrong, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_review_pilot, "ROOT", tmp_path)
    monkeypatch.setattr(run_review_pilot, "DATASETS", {"smoke_demo": "wrong.jsonl"})
    with pytest.raises(ValueError, match="Invalid review pilot case"):
        run_review_pilot.load_cases()


def test_pilot_never_reports_development_pass_when_provider_was_called(tmp_path, monkeypatch):
    from evals.runners import run_paired_model

    class RecordedCall:
        calls = 1

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(run_paired_model, "ProviderUsage", RecordedCall)
    summary = run_pilot(tmp_path)
    assert summary["provider_calls"] == 1
    assert summary["development_pass"] is False


def test_pilot_uses_mock_auth_when_caller_environment_is_oidc(tmp_path):
    env = {**os.environ, "AUTH_MODE": "oidc", "PYTHONPATH": "apps/api:."}
    code = ("from pathlib import Path; from evals.runners import run_review_pilot as pilot; import sys; "
            "pilot.load_cases = lambda: []; pilot.run_pilot(Path(sys.argv[1])); "
            "from resolveai.config import settings; print(settings.auth_mode)")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mock" in result.stdout


def test_pilot_rejects_preconfigured_cloud_exporter(tmp_path, monkeypatch):
    from resolveai import telemetry
    monkeypatch.setattr(telemetry, "_langfuse", object())
    with pytest.raises(RuntimeError, match="fresh process"):
        run_pilot(tmp_path)
