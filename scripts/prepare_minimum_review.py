"""Prepare a local, unreviewed v6 minimum-set packet for independent reviewers."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from resolveai.prompts import ROOT


SOURCES = (
    ("smoke_demo", "evals/datasets/smoke_demo.jsonl", None),
    ("core_business", "evals/datasets/core_business_dev_v1.jsonl", None),
    ("intent_route", "evals/datasets/intent_routing_dev_v1.jsonl", None),
    ("intent_dialogue", "evals/datasets/intent_dialogue_dev_v1.jsonl", None),
    ("policy_rag_positive", "evals/datasets/policy_retrieval_v1.jsonl", 20),
    ("policy_rag_negative", "evals/datasets/policy_retrieval_negatives_v1.jsonl", None),
    ("collaboration", "evals/datasets/collaboration_dev_v1.jsonl", None),
)


def load_packet_cases() -> tuple[list[dict], list[dict]]:
    cases: list[dict] = []
    source_meta: list[dict] = []
    seen: set[str] = set()
    for suite, relative_path, limit in SOURCES:
        path = ROOT / relative_path
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = rows[:limit] if limit is not None else rows
        source_meta.append({"suite": suite, "path": relative_path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "selected_cases": len(selected), "source_cases": len(rows)})
        for line_number, row in enumerate(selected, start=1):
            case_id = row["case_id"]
            if case_id in seen:
                raise ValueError(f"Duplicate case ID: {case_id}")
            seen.add(case_id)
            if row.get("split", "dev") != "dev":
                raise ValueError(f"Review packet cannot treat {case_id} as development data")
            if (row.get("schema_version") != "v1" or not row.get("group_keys")
                    or not row.get("source") or row.get("review", {}).get("status") != "pending"):
                raise ValueError(f"Review metadata incomplete for {case_id}")
            if suite == "intent_route":
                gold = {"route": row["route"], "acceptable_routes": row.get("acceptable_routes"), "intents": row["intents"]}
                input_data = {"message": row["message"]}
            elif suite.startswith("policy_rag"):
                gold = {"expected_clause_id": row["expected"]}
                input_data = {"query": row["query"]}
            else:
                gold = row["gold"]
                input_data = {"fixture": row.get("fixture"), "dialogue_script": row.get("dialogue_script"),
                              "terminal_scenario": row.get("terminal_scenario"), "workflow_steps": row.get("workflow_steps")}
            cases.append({"case_id": case_id, "suite": suite, "risk_tier": row.get("risk_tier", "normal"),
                          "split": "dev", "tags": row.get("tags", []), "group_keys": row.get("group_keys"),
                          "source": row.get("source", {"kind": "synthetic", "license": "project-original"}),
                          "source_path": relative_path, "source_line": line_number,
                          "input": input_data, "gold": gold, "review_status": "pending"})
    return cases, source_meta


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def group_component_sizes(cases: list[dict]) -> list[int]:
    """Find leakage-connected groups, including links across suites."""
    parent = list(range(len(cases)))
    first: dict[tuple[str, str], int] = {}

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, case in enumerate(cases):
        for key in ("customer", "order", "source", "template", "family"):
            value = case["group_keys"].get(key)
            if value in (None, "", "none"):
                continue
            pair = (key, str(value))
            if pair in first:
                parent[root(index)] = root(first[pair])
            else:
                first[pair] = index
    return sorted(Counter(root(index) for index in range(len(cases))).values(), reverse=True)


def group_component_counts(cases: list[dict]) -> dict[str, int]:
    """Count connected components within each suite for packet diagnostics."""
    by_suite: dict[str, list[dict]] = {}
    for case in cases:
        by_suite.setdefault(case["suite"], []).append(case)
    return {suite: len(group_component_sizes(rows)) for suite, rows in by_suite.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Local packet directory; defaults to ignored evals/review_packets/<timestamp>")
    args = parser.parse_args()
    cases, source_meta = load_packet_cases()
    minimum = {"smoke_demo": 25, "core_business": 30, "intent_route": 30,
               "policy_rag_positive": 20, "collaboration": 20}
    counts = Counter(case["suite"] for case in cases)
    missing = {suite: required for suite, required in minimum.items() if counts[suite] < required}
    if missing:
        raise ValueError(f"Minimum case counts missing: {missing}")
    normal = sorted((case for case in cases if case["risk_tier"] != "critical"), key=lambda case: case["case_id"])
    double_normal = {case["case_id"] for case in normal[:math.ceil(len(normal) * 0.2)]}
    second_review = {case["case_id"] for case in cases if case["risk_tier"] == "critical"} | double_normal
    output = args.output or ROOT / "evals/review_packets" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    packet_path = output / "review_cases.jsonl"
    packet_path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    fields = ["case_id", "suite", "source_path", "source_line", "risk_tier", "reviewer_id", "decision", "notes", "revised_gold_json", "reviewed_at_utc"]
    def assignment(case):
        return {key: case.get(key, "") for key in fields}
    write_csv(output / "reviewer_a.csv", [assignment(case) for case in cases], fields)
    write_csv(output / "reviewer_b.csv", [assignment(case) for case in cases if case["case_id"] in second_review], fields)
    write_csv(output / "adjudication.csv", [{"case_id": case["case_id"], "suite": case["suite"],
                                              "final_decision": "", "final_gold_json": "",
                                              "adjudicator_id": "", "notes": "", "reviewed_at_utc": ""}
                                             for case in cases],
              ["case_id", "suite", "final_decision", "final_gold_json",
               "adjudicator_id", "notes", "reviewed_at_utc"])
    components = group_component_counts(cases)
    all_component_sizes = group_component_sizes(cases)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "pending_independent_review",
                "packet_sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
                "source_datasets": source_meta, "case_count": len(cases), "suite_counts": dict(counts),
                "critical_count": sum(case["risk_tier"] == "critical" for case in cases),
                "normal_count": len(normal), "reviewer_a_assignments": len(cases),
                "reviewer_b_assignments": len(second_review), "normal_double_review_count": len(double_normal),
                "missing_group_keys": sum(not case["group_keys"] for case in cases),
                "group_components_by_suite": components,
                "group_components_all_suites": len(all_component_sizes),
                "largest_group_component_cases": max(all_component_sizes),
                "grouped_locked_split_ready": False,
                "locked_cases": 0}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Independent review packet\n\n"
        "Use the versioned human/model [review criteria v3](../../../docs/implementation/eval-review-criteria-v3.md), grounded in v6 and the checked-in synthetic seed. "
        "All cases are synthetic author-written development fixtures. No case is locked. Review each assigned case in `review_cases.jsonl` against its `source_path` and the v6 policy/business rules. "
        "Record `accept`, `revise`, `reject`, or `needs_context` plus a reason and reviewer identity in your assigned CSV. Review independently before seeing the other reviewer's sheet or AI suggestions. `needs_context` means a material claim cannot be checked from the supplied source and fixture; explain the missing fact. "
        "Critical cases require two reviewers; `reviewer_b.csv` also contains at least 20% of normal cases. "
        "Check customer ownership, seven-day boundaries, explicit confirmation, approval-before-refund, idempotency, source/version validity, missing-evidence behavior, and final database/audit gold where applicable. "
        "Set `reviewer_id` to your human identity and `reviewed_at_utc` to an ISO-8601 UTC timestamp; explain each decision in `notes`. For `revise`, put a complete corrected gold object in `revised_gold_json` when possible. Record risk-tier concerns in notes. "
        "Record the adjudicated result in `adjudication.csv`; for a final `revise`, place the complete corrected gold object in `final_gold_json`. Do not directly change source labels during review. "
        "Only after disagreements are resolved should a separate grouped dev/locked split be created and the locked suite run against frozen code, model, Prompt, policy, fixture, and scorer hashes. "
        "Cases sharing an order, customer, source conversation, or template family must remain in one partition. A shared policy bundle alone does not connect cases. The manifest reports connected group counts within and across suites; these development fixtures cannot be relabeled into an independent locked partition.\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), **{key: manifest[key] for key in ("case_count", "critical_count", "normal_count", "reviewer_b_assignments", "missing_group_keys", "group_components_by_suite", "group_components_all_suites", "largest_group_component_cases", "locked_cases")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
