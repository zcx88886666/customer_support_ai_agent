from __future__ import annotations

import csv
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
    assert all(case["split"] == "dev" and case["review_status"] == "pending" for case in cases)
