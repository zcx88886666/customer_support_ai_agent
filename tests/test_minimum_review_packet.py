from __future__ import annotations

import csv
import hashlib
import json
import math
import sys

from scripts import prepare_minimum_review


def test_minimum_review_packet_assigns_two_critical_reviews(tmp_path, monkeypatch):
    output = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(output)])
    prepare_minimum_review.main()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in (output / "review_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    with (output / "reviewer_a.csv").open(encoding="utf-8", newline="") as stream:
        reviewer_a = list(csv.DictReader(stream))
    with (output / "reviewer_b.csv").open(encoding="utf-8", newline="") as stream:
        reviewer_b = list(csv.DictReader(stream))
    critical_ids = {case["case_id"] for case in cases if case["risk_tier"] == "critical"}
    b_ids = {row["case_id"] for row in reviewer_b}
    assert len(cases) == len(reviewer_a) == manifest["case_count"]
    assert critical_ids <= b_ids
    assert len(b_ids - critical_ids) >= math.ceil(manifest["normal_count"] * 0.2)
    assert manifest["locked_cases"] == 0
    assert manifest["missing_group_keys"] == 0
    assert manifest["grouped_locked_split_ready"] is False
    assert all(count >= 1 for count in manifest["group_components_by_suite"].values())
    assert manifest["group_components_all_suites"] == 2
    assert manifest["largest_group_component_cases"] == 123
    assert all(case["split"] == "dev" and case["review_status"] == "pending" for case in cases)
    assert manifest["packet_sha256"] == hashlib.sha256((output / "review_cases.jsonl").read_bytes()).hexdigest()
    with (output / "adjudication.csv").open(encoding="utf-8", newline="") as stream:
        adjudication = csv.DictReader(stream)
        assert "final_gold_json" in adjudication.fieldnames


def test_group_components_join_shared_keys_across_suites():
    cases = [
        {"case_id": "a", "suite": "intent_route", "group_keys": {"customer": "c1"}},
        {"case_id": "b", "suite": "core_business", "group_keys": {"customer": "c1"}},
        {"case_id": "c", "suite": "core_business", "group_keys": {"customer": "c2"}},
    ]
    assert prepare_minimum_review.group_component_sizes(cases) == [2, 1]


def test_group_components_join_shared_template_family_across_suites():
    cases = [
        {"case_id": "a", "suite": "smoke_demo", "group_keys": {"family": "refund_request"}},
        {"case_id": "b", "suite": "intent_route", "group_keys": {"family": "refund_request"}},
        {"case_id": "c", "suite": "intent_route", "group_keys": {"family": "policy_qa"}},
    ]
    assert prepare_minimum_review.group_component_sizes(cases) == [2, 1]
