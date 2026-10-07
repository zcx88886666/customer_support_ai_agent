"""The review packet validator checks forms without promoting development gold."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys

from resolveai.prompts import ROOT
from scripts import prepare_minimum_review
from scripts.validate_minimum_review import _valid_gold


def packet(tmp_path, monkeypatch):
    output = tmp_path / "packet"
    monkeypatch.setattr(sys, "argv", ["prepare_minimum_review.py", "--output", str(output)])
    prepare_minimum_review.main()
    return output


def test_revised_gold_schema_accepts_all_current_suite_gold():
    cases, _ = prepare_minimum_review.load_packet_cases()
    assert all(_valid_gold(case["suite"], case["gold"], case) for case in cases)


def test_revised_gold_schema_rejects_missing_dialogue_terminal_checks(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    malformed = json.dumps({"turns": [{}], "final": {"x": 1}})
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({
        "decision": "revise", "revised_gold_json": malformed})
             if row["case_id"] == "intent-dialogue-return-slots" else None)
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "final_decision": "revise", "final_gold_json": malformed})
             if row["case_id"] == "intent-dialogue-return-slots" else None)
    code, report = validate(output)
    assert code == 1
    assert "final_gold_shape_invalid" in report["errors"]


def test_revised_gold_schema_requires_per_turn_checks_for_multi_turn_core(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    cases = [json.loads(line) for line in (output / "review_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    gold = next(row["gold"] for row in cases if row["case_id"] == "core-chat-approved-refund")
    malformed = json.dumps({key: value for key, value in gold.items() if key != "turn_gold"})
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({
        "decision": "revise", "revised_gold_json": malformed})
             if row["case_id"] == "core-chat-approved-refund" else None)
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "final_decision": "revise", "final_gold_json": malformed})
             if row["case_id"] == "core-chat-approved-refund" else None)
    code, report = validate(output)
    assert code == 1
    assert "final_gold_shape_invalid" in report["errors"]


def validate(output):
    env = {**os.environ, "PYTHONPATH": "apps/api:."}
    result = subprocess.run(
        [sys.executable, "scripts/validate_minimum_review.py", str(output)],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    return result.returncode, json.loads(result.stdout) if result.stdout else {}


def edit_csv(path, change):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fields = list(rows[0])
    for row in rows:
        change(row)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def accept_all(output):
    for sheet, reviewer in (("reviewer_a.csv", "human-a"), ("reviewer_b.csv", "human-b")):
        edit_csv(output / sheet, lambda row: row.update({
            "reviewer_id": reviewer, "decision": "accept", "notes": "Checked against source and rubric",
            "reviewed_at_utc": "2026-10-07T12:00:00Z"}))
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "final_decision": "accept", "adjudicator_id": "human-c",
        "notes": "Accepted reviewed gold", "reviewed_at_utc": "2026-10-07T13:00:00Z"}))


def test_generated_packet_is_pending_and_cannot_be_locked(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    code, report = validate(output)
    assert code == 0
    assert report["status"] == "pending"
    assert report["reviewer_a_completed"] == 0
    assert report["reviewer_b_completed"] == 0
    assert report["locked_release_pass"] is False


def test_complete_distinct_review_forms_are_still_not_locked_gold(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    code, report = validate(output)
    assert code == 0
    assert report["status"] == "forms_complete"
    assert report["accepted_cases"] == 163
    assert report["reviewer_b_completed"] == 89
    assert report["ready_for_locked_split"] is False
    assert report["locked_release_pass"] is False


def test_same_reviewer_on_both_required_sheets_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    edit_csv(output / "reviewer_b.csv", lambda row: row.update({"reviewer_id": "human-a"}))
    code, report = validate(output)
    assert code == 1
    assert report["status"] == "invalid"
    assert "reviewer_identity_conflict" in report["errors"]


def test_tampered_case_packet_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    with (output / "review_cases.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    code, report = validate(output)
    assert code == 1
    assert report["status"] == "invalid"
    assert "packet_hash_mismatch" in report["errors"]


def test_packet_gold_changed_with_updated_manifest_hash_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    case_path = output / "review_cases.jsonl"
    cases = [json.loads(line) for line in case_path.read_text(encoding="utf-8").splitlines()]
    cases[0]["gold"] = {"forged": True}
    case_path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
                         encoding="utf-8")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packet_sha256"] = hashlib.sha256(case_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert "packet_source_mismatch" in report["errors"]


def test_removed_source_manifest_entry_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source_datasets"] = manifest["source_datasets"][:1]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert "source_manifest_mismatch" in report["errors"]


def test_tampered_group_diagnostic_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["group_components_all_suites"] = 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert "group_manifest_mismatch" in report["errors"]


def test_packet_manifest_cannot_claim_locked_cases(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["grouped_locked_split_ready"] = True
    manifest["locked_cases"] = manifest["case_count"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert "premature_locked_manifest" in report["errors"]


def test_revise_requires_complete_json_gold(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({"decision": "revise"})
             if row["case_id"] == "core-chat-expired-window" else None)
    code, report = validate(output)
    assert code == 1
    assert report["status"] == "invalid"
    assert "missing_revised_gold" in report["errors"]


def test_arbitrary_revised_gold_shape_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({
        "decision": "revise", "revised_gold_json": '{"x":1}'})
             if row["case_id"] == "core-chat-expired-window" else None)
    code, report = validate(output)
    assert code == 1
    assert "revised_gold_shape_invalid" in report["errors"]


def test_revise_can_correct_gold_structure_when_suite_contract_is_complete(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    corrected = json.dumps({"route": "knowledge", "specialists": ["policy"],
                            "must_not_contain": ["refund issued"], "ledger_count": 0})
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({
        "decision": "revise", "revised_gold_json": corrected})
             if row["case_id"] == "smoke-01" else None)
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "final_decision": "revise", "final_gold_json": corrected})
             if row["case_id"] == "smoke-01" else None)
    code, report = validate(output)
    assert code == 0
    assert report["status"] == "forms_complete"


def test_non_revision_with_corrected_gold_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    edit_csv(output / "reviewer_a.csv", lambda row: row.update({
        "revised_gold_json": '{"x":1}'})
             if row["case_id"] == "smoke-01" else None)
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "final_gold_json": '{"x":1}'})
             if row["case_id"] == "smoke-01" else None)
    code, report = validate(output)
    assert code == 1
    assert "unexpected_revised_gold" in report["errors"]
    assert "unexpected_final_gold" in report["errors"]


def test_new_packet_requires_adjudication_gold_column(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    path = output / "adjudication.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    with path.open("w", encoding="utf-8", newline="") as stream:
        fields = [key for key in rows[0] if key != "final_gold_json"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    code, report = validate(output)
    assert code == 1
    assert "sheet_columns_missing" in report["errors"]


def test_adjudication_must_follow_reviews_and_reconcile_rejections(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    accept_all(output)
    for sheet in ("reviewer_a.csv", "reviewer_b.csv"):
        edit_csv(output / sheet, lambda row: row.update({"decision": "reject"})
                 if row["case_id"] == "core-chat-expired-window" else None)
    edit_csv(output / "adjudication.csv", lambda row: row.update({
        "reviewed_at_utc": "2026-10-07T11:00:00Z"})
             if row["case_id"] == "core-chat-expired-window" else None)
    code, report = validate(output)
    assert code == 1
    assert "adjudication_before_review" in report["errors"]
    assert "unresolved_review_disagreement" in report["errors"]


def test_legacy_packet_without_hash_or_final_gold_column_is_pending(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["packet_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    adjudication_path = output / "adjudication.csv"
    with adjudication_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    with adjudication_path.open("w", encoding="utf-8", newline="") as stream:
        fields = [key for key in rows[0] if key != "final_gold_json"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    code, report = validate(output)
    assert code == 0
    assert report["status"] == "pending"


def test_malformed_manifest_returns_invalid_report(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    (output / "manifest.json").write_text("[]", encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert report["status"] == "invalid"


def test_changed_source_dataset_is_invalid(tmp_path, monkeypatch):
    output = packet(tmp_path, monkeypatch)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source_datasets"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, report = validate(output)
    assert code == 1
    assert report["status"] == "invalid"
    assert "source_hash_mismatch" in report["errors"]
