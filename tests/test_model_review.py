from __future__ import annotations

import csv
import json

import pytest

from scripts.run_model_review import CaseReview, compare_human, json_pointer, seed_snapshot, validate_review


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
