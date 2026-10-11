"""Validate one human review stream without promoting a v6 locked release."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from scripts.validate_minimum_review import validate_packet


def validate_single_human_review(packet: Path) -> dict:
    report = validate_packet(packet)
    result = {**report, "review_standard": "single_human_development",
              "model_review_counts_as_human": False,
              "second_human_required_for_v6": True,
              "ready_for_locked_split": False, "locked_release_pass": False}
    if report["status"] != "invalid":
        with (packet / "reviewer_a.csv").open(encoding="utf-8", newline="") as stream:
            identities = {row["reviewer_id"].strip() for row in csv.DictReader(stream)
                          if row["decision"].strip()}
        errors = set(result["errors"])
        if len(identities) > 1:
            errors.add("single_reviewer_identity_conflict")
        if any(identity.lower().startswith(("ai:", "model:", "openrouter:")) for identity in identities):
            errors.add("single_reviewer_not_human")
        if errors:
            result["status"] = "invalid"
            result["errors"] = sorted(errors)
    if (report["status"] == "pending" and report.get("reviewer_a_completed") == report.get("case_count")
            and report.get("reviewer_b_completed") == 0
            and report.get("adjudication_completed") == 0 and result["status"] != "invalid"):
        result["status"] = "single_review_complete"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    report = validate_single_human_review(args.packet)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if report["status"] == "invalid" else 0)


if __name__ == "__main__":
    main()
