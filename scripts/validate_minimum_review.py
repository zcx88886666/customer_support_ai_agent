"""Validate local review packet forms without promoting development cases to locked gold."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from scripts.prepare_minimum_review import load_packet_cases


DECISIONS = {"accept", "revise", "reject", "needs_context"}
REVIEW_FIELDS = {"case_id", "suite", "source_path", "source_line", "risk_tier",
                 "reviewer_id", "decision", "notes", "revised_gold_json", "reviewed_at_utc"}
ADJUDICATION_FIELDS = {"case_id", "suite", "final_decision",
                       "adjudicator_id", "notes", "reviewed_at_utc"}


def _csv_rows(path: Path, required: set[str], errors: set[str]) -> dict[str, dict]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not required <= set(reader.fieldnames):
                errors.add("sheet_columns_missing")
                return {}
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error):
        errors.add("sheet_unreadable")
        return {}
    ids = [row.get("case_id") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        errors.add("sheet_case_ids_invalid")
    return {row["case_id"]: row for row in rows if row.get("case_id")}


def _valid_utc(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _is_int(value) -> bool:
    return type(value) is int and value >= 0


def _is_strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_gold(suite: str, gold: dict, case: dict | None = None) -> bool:
    """Check fields consumed by the local suite; semantic correctness remains human work."""
    if not isinstance(gold, dict) or not gold:
        return False
    if suite in {"smoke_demo", "collaboration"}:
        allowed = {"ledger_count", "route", "specialists", "must_contain", "must_not_contain",
                   "status", "must_not_status", "error_code", "reason_code", "ticket_count",
                   "order_read_scope"}
        if not set(gold) <= allowed or not _is_int(gold.get("ledger_count")):
            return False
        if suite == "collaboration" and not {"route", "specialists"} <= set(gold):
            return False
        if suite == "smoke_demo" and not (set(gold) - {"ledger_count"}):
            return False
        return all(_is_strings(gold[key]) for key in ("specialists", "must_contain", "must_not_contain")
                   if key in gold) and all(isinstance(gold[key], str) and gold[key] for key in
                                           ("route", "status", "must_not_status", "error_code",
                                            "reason_code", "order_read_scope") if key in gold) and all(
                                                _is_int(gold[key]) for key in ("ticket_count",) if key in gold)
    if suite == "core_business":
        required = {"chat_http_status", "chat_status", "chat_route", "chat_return_count",
                    "chat_ledger_count", "http_statuses", "return_count", "return_statuses",
                    "proposal_statuses", "approval_decisions", "ledger_count", "order_version",
                    "audit_actions"}
        allowed = required | {"chat_error_code", "chat_reason_code", "chat_ticket_count",
                              "expected_observations", "linked_exception_ticket", "ticket_count",
                              "turn_gold"}
        turn_gold = gold.get("turn_gold")
        script = case.get("input", {}).get("dialogue_script") if case else None
        if script is not None and len(script) > 1 and turn_gold is None:
            return False
        if turn_gold is not None:
            if (not isinstance(turn_gold, list) or (script is not None and len(turn_gold) != len(script))
                    or any(not isinstance(turn, dict)
                           or not {"status", "return_count", "ledger_count"} <= set(turn)
                           or not isinstance(turn["status"], str)
                           or not _is_int(turn["return_count"]) or not _is_int(turn["ledger_count"])
                           for turn in turn_gold)):
                return False
        return (required <= set(gold) <= allowed
                and all(_is_int(gold[key]) for key in ("chat_http_status", "chat_return_count",
                                                        "chat_ledger_count", "return_count",
                                                        "ledger_count", "order_version"))
                and all(gold[key] is None or isinstance(gold[key], str)
                        for key in ("chat_status", "chat_route"))
                and isinstance(gold["http_statuses"], dict)
                and all(isinstance(name, str) and _is_int(status)
                        for name, status in gold["http_statuses"].items())
                and all(_is_strings(gold[key]) for key in
                        ("return_statuses", "proposal_statuses", "approval_decisions"))
                and isinstance(gold["audit_actions"], dict)
                and all(isinstance(name, str) and _is_int(count)
                        for name, count in gold["audit_actions"].items())
                and all(_is_int(gold[key]) for key in ("chat_ticket_count", "ticket_count") if key in gold)
                and all(isinstance(gold[key], dict) for key in ("expected_observations",) if key in gold)
                and all(isinstance(gold[key], bool) for key in ("linked_exception_ticket",) if key in gold)
                )
    if suite == "intent_route":
        return (set(gold) == {"route", "acceptable_routes", "intents"}
                and isinstance(gold["route"], str) and bool(gold["route"])
                and _is_strings(gold["intents"])
                and (gold["acceptable_routes"] is None or _is_strings(gold["acceptable_routes"])))
    if suite == "intent_dialogue":
        if set(gold) != {"turns", "final"} or not isinstance(gold["turns"], list):
            return False
        script = case.get("input", {}).get("dialogue_script") if case else None
        if not gold["turns"] or (script is not None and len(gold["turns"]) != len(script)):
            return False
        for turn in gold["turns"]:
            if (not isinstance(turn, dict)
                    or not {"http_status", "status", "return_count", "ledger_count", "ticket_count"} <= set(turn)
                    or turn["status"] is not None and not isinstance(turn["status"], str)
                    or any(not _is_int(turn[key]) for key in
                           ("http_status", "return_count", "ledger_count", "ticket_count"))):
                return False
        final = gold["final"]
        return (isinstance(final, dict)
                and {"return_count", "ledger_count", "ticket_count", "audit_actions"} <= set(final)
                and all(_is_int(final[key]) for key in ("return_count", "ledger_count", "ticket_count"))
                and isinstance(final["audit_actions"], dict)
                and all(isinstance(action, str) and _is_int(count)
                        for action, count in final["audit_actions"].items()))
    if suite.startswith("policy_rag_"):
        return (set(gold) == {"expected_clause_id"}
                and (gold["expected_clause_id"] is None or
                     isinstance(gold["expected_clause_id"], str) and bool(gold["expected_clause_id"])))
    return False


def _gold_object(value: str, case: dict) -> bool:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return False
    return _valid_gold(case["suite"], parsed, case)


def _review_row(row: dict, case: dict, errors: set[str]) -> bool:
    decision = (row.get("decision") or "").strip()
    supplied = any((row.get(field) or "").strip() for field in
                   ("reviewer_id", "notes", "revised_gold_json", "reviewed_at_utc"))
    if not decision:
        if supplied:
            errors.add("partial_review")
        return False
    if decision not in DECISIONS:
        errors.add("review_decision_invalid")
        return False
    if not all((row.get(field) or "").strip() for field in ("reviewer_id", "notes")):
        errors.add("review_attribution_missing")
    if not _valid_utc(row.get("reviewed_at_utc") or ""):
        errors.add("review_time_invalid")
    if decision == "revise":
        if not (row.get("revised_gold_json") or "").strip():
            errors.add("missing_revised_gold")
        elif not _gold_object(row["revised_gold_json"], case):
            errors.add("revised_gold_shape_invalid")
    elif (row.get("revised_gold_json") or "").strip():
        errors.add("unexpected_revised_gold")
    return True


def _adjudication_row(row: dict, case: dict, errors: set[str]) -> bool:
    decision = (row.get("final_decision") or "").strip()
    supplied = any((row.get(field) or "").strip() for field in
                   ("adjudicator_id", "notes", "final_gold_json", "reviewed_at_utc"))
    if not decision:
        if supplied:
            errors.add("partial_adjudication")
        return False
    if decision not in DECISIONS:
        errors.add("adjudication_decision_invalid")
        return False
    if not all((row.get(field) or "").strip() for field in ("adjudicator_id", "notes")):
        errors.add("adjudication_attribution_missing")
    if not _valid_utc(row.get("reviewed_at_utc") or ""):
        errors.add("adjudication_time_invalid")
    if decision == "revise":
        if not (row.get("final_gold_json") or "").strip():
            errors.add("missing_final_gold")
        elif not _gold_object(row["final_gold_json"], case):
            errors.add("final_gold_shape_invalid")
    elif (row.get("final_gold_json") or "").strip():
        errors.add("unexpected_final_gold")
    return True


def validate_packet(packet: Path) -> dict:
    errors: set[str] = set()
    try:
        manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
        packet_bytes = (packet / "review_cases.jsonl").read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "invalid", "errors": ["packet_unreadable"], "locked_release_pass": False}
    if not isinstance(manifest, dict):
        return {"status": "invalid", "errors": ["manifest_shape_invalid"], "locked_release_pass": False}
    expected_hash = manifest.get("packet_sha256")
    if expected_hash is not None and hashlib.sha256(packet_bytes).hexdigest() != expected_hash:
        errors.add("packet_hash_mismatch")
    try:
        expected_cases, expected_sources = load_packet_cases()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"status": "invalid", "errors": ["source_unavailable"], "locked_release_pass": False}
    listed_sources = manifest.get("source_datasets")
    if not isinstance(listed_sources, list):
        errors.add("source_manifest_mismatch")
    elif listed_sources != expected_sources:
        errors.add("source_manifest_mismatch")
        if any(isinstance(row, dict) and row.get("path") == source["path"]
               and row.get("sha256") != source["sha256"]
               for row in listed_sources for source in expected_sources):
            errors.add("source_hash_mismatch")

    try:
        cases = [json.loads(line) for line in packet_bytes.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError):
        return {"status": "invalid", "errors": ["case_json_invalid"], "locked_release_pass": False}
    if not all(isinstance(case, dict) for case in cases):
        errors.add("case_shape_invalid")
    elif cases != expected_cases:
        errors.add("packet_source_mismatch")
    if errors:
        return {"status": "invalid", "errors": sorted(errors), "locked_release_pass": False}
    ids = [case.get("case_id") for case in cases]
    if (not cases or not all(isinstance(case_id, str) and case_id for case_id in ids)
            or len(ids) != len(set(ids)) or len(cases) != manifest.get("case_count")):
        errors.add("case_manifest_mismatch")
    if any(case.get("split") != "dev" or case.get("review_status") != "pending" for case in cases):
        errors.add("case_prematurely_promoted")
    by_id = {case["case_id"]: case for case in cases if isinstance(case.get("case_id"), str)}
    normal = sorted((case for case in cases if case.get("risk_tier") != "critical"),
                    key=lambda case: case.get("case_id", ""))
    required_b = {case["case_id"] for case in cases if case.get("risk_tier") == "critical"}
    required_b.update(case["case_id"] for case in normal[:math.ceil(len(normal) * 0.2)])
    final_fields = ADJUDICATION_FIELDS | ({"final_gold_json"} if expected_hash is not None else set())
    sheets = {
        "a": _csv_rows(packet / "reviewer_a.csv", REVIEW_FIELDS, errors),
        "b": _csv_rows(packet / "reviewer_b.csv", REVIEW_FIELDS, errors),
        "final": _csv_rows(packet / "adjudication.csv", final_fields, errors),
    }
    if set(sheets["a"]) != set(by_id) or set(sheets["b"]) != required_b or set(sheets["final"]) != set(by_id):
        errors.add("assignment_mismatch")
    for name in ("a", "b"):
        for case_id, row in sheets[name].items():
            case = by_id.get(case_id)
            if case and any(row.get(field) != str(case.get(field, "")) for field in
                            ("suite", "source_path", "source_line", "risk_tier")):
                errors.add("review_case_metadata_mismatch")
    for case_id, row in sheets["final"].items():
        if case_id in by_id and row.get("suite") != by_id[case_id].get("suite"):
            errors.add("adjudication_case_metadata_mismatch")

    a_completed = sum(_review_row(row, by_id[case_id], errors)
                      for case_id, row in sheets["a"].items() if case_id in by_id)
    b_completed = sum(_review_row(row, by_id[case_id], errors)
                      for case_id, row in sheets["b"].items() if case_id in by_id)
    final_completed = sum(_adjudication_row(row, by_id[case_id], errors)
                          for case_id, row in sheets["final"].items() if case_id in by_id)
    for case_id in required_b & set(sheets["a"]) & set(sheets["b"]):
        first = (sheets["a"][case_id].get("reviewer_id") or "").strip()
        second = (sheets["b"][case_id].get("reviewer_id") or "").strip()
        if first and second and first == second:
            errors.add("reviewer_identity_conflict")
    for case_id, final in sheets["final"].items():
        if case_id not in by_id or not (final.get("final_decision") or "").strip():
            continue
        reviews = [sheets["a"].get(case_id)]
        if case_id in required_b:
            reviews.append(sheets["b"].get(case_id))
        if any(not row or not (row.get("decision") or "").strip() for row in reviews):
            errors.add("adjudication_before_reviews")
            continue
        final_time = datetime.fromisoformat(final["reviewed_at_utc"].replace("Z", "+00:00")) if _valid_utc(final.get("reviewed_at_utc") or "") else None
        if final_time and any(_valid_utc(row.get("reviewed_at_utc") or "") and
                              final_time < datetime.fromisoformat(row["reviewed_at_utc"].replace("Z", "+00:00"))
                              for row in reviews):
            errors.add("adjudication_before_review")
        if final.get("final_decision") == "accept" and any(row.get("decision") != "accept" for row in reviews):
            errors.add("unresolved_review_disagreement")
    all_complete = (a_completed == len(by_id) and b_completed == len(required_b)
                    and final_completed == len(by_id))
    accepted = sum(row.get("final_decision") in {"accept", "revise"}
                   for row in sheets["final"].values())
    return {"status": "invalid" if errors else "forms_complete" if all_complete else "pending",
            "errors": sorted(errors), "case_count": len(by_id),
            "reviewer_a_completed": a_completed, "reviewer_b_completed": b_completed,
            "adjudication_completed": final_completed, "accepted_cases": accepted,
            "ready_for_locked_split": False, "locked_release_pass": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    report = validate_packet(args.packet)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if report["status"] == "invalid" else 0)


if __name__ == "__main__":
    main()
