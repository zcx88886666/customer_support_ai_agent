"""One human can validate a development review without passing the v6 release gate."""

from __future__ import annotations

import csv
import sys

from scripts import prepare_minimum_review
from scripts.validate_single_human_review import validate_single_human_review


def make_packet(tmp_path, monkeypatch):
    packet = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(packet)])
    prepare_minimum_review.main()
    return packet


def fill_first_sheet(packet):
    path = packet / "reviewer_a.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row.update({"reviewer_id": "human-1", "decision": "accept",
                    "notes": "Checked source facts and expected outcome",
                    "reviewed_at_utc": "2026-10-10T12:00:00Z"})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_one_complete_human_sheet_is_single_reviewed_only(tmp_path, monkeypatch):
    packet = make_packet(tmp_path, monkeypatch)
    fill_first_sheet(packet)
    result = validate_single_human_review(packet)
    assert result["status"] == "single_review_complete"
    assert result["reviewer_a_completed"] == result["case_count"]
    assert result["locked_release_pass"] is False
    assert result["ready_for_locked_split"] is False


def test_incomplete_or_tampered_first_sheet_is_not_complete(tmp_path, monkeypatch):
    packet = make_packet(tmp_path, monkeypatch)
    assert validate_single_human_review(packet)["status"] == "pending"
    fill_first_sheet(packet)
    case_path = packet / "review_cases.jsonl"
    case_path.write_text(case_path.read_text(encoding="utf-8") + "\n{}\n", encoding="utf-8")
    result = validate_single_human_review(packet)
    assert result["status"] == "invalid"
    assert result["locked_release_pass"] is False


def test_one_human_cannot_fill_both_reviewer_roles(tmp_path, monkeypatch):
    packet = make_packet(tmp_path, monkeypatch)
    fill_first_sheet(packet)
    second = packet / "reviewer_b.csv"
    with second.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rows[0].update({"reviewer_id": "human-1", "decision": "accept",
                    "notes": "Duplicated review", "reviewed_at_utc": "2026-10-10T12:00:00Z"})
    with second.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = validate_single_human_review(packet)
    assert result["status"] == "invalid"
    assert "reviewer_identity_conflict" in result["errors"]


def test_single_human_sheet_rejects_multiple_or_model_identities(tmp_path, monkeypatch):
    packet = make_packet(tmp_path, monkeypatch)
    fill_first_sheet(packet)
    path = packet / "reviewer_a.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rows[0]["reviewer_id"] = "human-2"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = validate_single_human_review(packet)
    assert result["status"] == "invalid"
    assert "single_reviewer_identity_conflict" in result["errors"]

    rows[0]["reviewer_id"] = "ai:openrouter:reviewer"
    for row in rows[1:]:
        row["reviewer_id"] = "ai:openrouter:reviewer"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = validate_single_human_review(packet)
    assert result["status"] == "invalid"
    assert "single_reviewer_not_human" in result["errors"]
