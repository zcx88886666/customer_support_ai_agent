from __future__ import annotations

import csv
import json
import sys

import httpx
import pytest

from scripts import prepare_minimum_review, run_model_review
from scripts.run_model_review import CaseReview, ModelResponseError, compare_human, json_pointer, seed_snapshot, validate_review


def review(**overrides):
    data = {"case_id": "one", "decision": "accept", "risk_tier_assessment": "normal",
            "summary": "Supported", "findings": [], "missing_context": [], "confidence_percent": 80}
    data.update(overrides)
    return CaseReview.model_validate(data)


def test_review_audits_unsubstantiated_quote():
    material = {"case": {"case_id": "one", "gold": {"route": "knowledge"}}}
    finding = {"criterion": "routing", "severity": "major", "evidence_path": "/case/gold/route",
               "evidence_quote": "after_sales", "explanation": "Wrong route", "suggested_change": "Use clarify"}
    assert validate_review(review(decision="revise", findings=[finding]), material["case"], material)[0]["quote_matches"] is False
    finding["evidence_quote"] = "knowledge"
    assert validate_review(review(decision="revise", findings=[finding]), material["case"], material)[0]["quote_matches"] is True
    assert json_pointer(material, "/case/gold/route") == "knowledge"


def test_review_decisions_require_reason():
    with pytest.raises(ValueError):
        review(decision="revise")
    with pytest.raises(ValueError):
        review(decision="needs_context")
    with pytest.raises(ValueError):
        review(decision="accept", findings=[{"criterion": "x", "severity": "minor", "evidence_path": "/case",
                                               "evidence_quote": "", "explanation": "x", "suggested_change": "x"}])


def test_human_model_disagreement_uses_same_decisions(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    with (packet / "reviewer_a.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "decision"])
        writer.writeheader()
        writer.writerow({"case_id": "one", "decision": "accept"})
    cases = [{"case_id": "one", "suite": "smoke_demo", "risk_tier": "normal"}]
    results = {"one": {"review": review(decision="needs_context", missing_context=["Source order status"]).model_dump()}}
    output = tmp_path / "disagreements.csv"
    assert compare_human(packet, cases, results, output) == 1
    assert list(csv.DictReader(output.open(encoding="utf-8")))[0]["model_decision"] == "needs_context"


def test_reference_facts_come_from_seed_not_proposed_gold():
    snapshot = seed_snapshot()
    assert snapshot["demo-order-01"]["customer_id"] == "cust-01"
    assert snapshot["demo-order-05"]["customer_id"] == "cust-02"
    assert snapshot["demo-order-01"]["items"][0]["paid_cents"] == 1018
    assert snapshot["demo-order-01"]["shipments"][0]["delivered_at_utc"].endswith("+00:00")


def test_refresh_only_never_loads_api_key(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(packet)])
    prepare_minimum_review.main()
    def forbidden_key():
        raise AssertionError("Refresh must not access OpenRouter credentials")
    monkeypatch.setattr(run_model_review, "load_key", forbidden_key)
    monkeypatch.setattr(sys, "argv", ["run_model_review.py", str(packet), "--refresh-only"])
    assert run_model_review.main() == 0
    manifest = json.loads((packet / "model_review_gpt6_luna/manifest.json").read_text(encoding="utf-8"))
    assert manifest["reviewed"] == 0 and manifest["pending"] == 162
    assert manifest["model"] == "openai/gpt-6-luna"
    assert (packet / "model_review_gpt6_luna/human_model_disagreements.csv").exists()


def test_sol_review_uses_own_output_and_prices(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(packet)])
    prepare_minimum_review.main()
    monkeypatch.setattr(sys, "argv", ["run_model_review.py", str(packet), "--refresh-only",
                                       "--model", "openai/gpt-6-sol"])
    assert run_model_review.main() == 0
    manifest = json.loads((packet / "model_review_gpt6_sol/manifest.json").read_text(encoding="utf-8"))
    assert manifest["model"] == "openai/gpt-6-sol"
    assert manifest["list_price_input_per_m"] == 2.0
    assert manifest["list_price_output_per_m"] == 10.0
    assert not (packet / "model_review_gpt6_luna").exists()


def test_invalid_model_evidence_retries_once_and_counts_failed_cost(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(packet)])
    prepare_minimum_review.main()
    monkeypatch.setattr(run_model_review, "load_key", lambda: "synthetic-key")
    attempts = 0

    def respond(client, key, content, case, material, max_tokens):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelResponseError("Invalid structured review: bad pointer",
                                     {"input_tokens": 50, "output_tokens": 20, "cost_usd": 0.00002})
        return review(case_id=case["case_id"]), {"input_tokens": 50, "output_tokens": 10,
                                                "cost_usd": 0.00001}, []

    monkeypatch.setattr(run_model_review, "request_review", respond)
    monkeypatch.setattr(sys, "argv", ["run_model_review.py", str(packet), "--case-id", "smoke-01", "--limit", "1"])
    assert run_model_review.main() == 0
    manifest = json.loads((packet / "model_review_gpt6_luna/manifest.json").read_text(encoding="utf-8"))
    assert attempts == 2
    assert manifest["reviewed"] == 1 and manifest["failed_calls"] == 1
    assert manifest["usage"]["total_cost_usd"] == 0.00003


def test_deepseek_request_keeps_strict_schema_and_usage(monkeypatch):
    monkeypatch.setattr(run_model_review, "MODEL", "deepseek/deepseek-v3.2")
    captured = {}
    expected = review().model_dump()

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(expected)},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001},
                                         "provider": "synthetic"})

    material = {"case": {"case_id": "one"}}
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result, usage, audit = run_model_review.request_review(client, "synthetic-test-key", "synthetic prompt",
                                                               material["case"], material, 3000)
    assert captured["model"] == "deepseek/deepseek-v3.2"
    assert captured["provider"]["require_parameters"] is True
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert result.decision == "accept" and audit == []
    assert usage["cost_usd"] == 0.0001 and usage["cost_source"] == "provider"
