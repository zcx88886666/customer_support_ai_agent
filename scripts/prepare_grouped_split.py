"""Prepare a review-complete, leakage-safe split candidate; never pass a release gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from resolveai.prompts import ROOT
from scripts.prepare_minimum_review import group_components
from scripts.validate_minimum_review import validate_packet


MINIMUM_BY_SUITE = {"smoke_demo": 25, "core_business": 30, "intent_route": 30,
                    "intent_dialogue": 12, "policy_rag_positive": 20,
                    "policy_rag_negative": 20, "collaboration": 20}


def plan_grouped_split(cases: list[dict], *, locked_target: float = 0.4) -> dict[str, str]:
    """Select whole leakage components for a 30–50% locked candidate."""
    if not cases or not 0.30 <= locked_target <= 0.50:
        raise ValueError("Split needs cases and a locked target between 0.30 and 0.50")
    case_ids = [case.get("case_id") for case in cases]
    if (not all(isinstance(case_id, str) and case_id for case_id in case_ids)
            or len(case_ids) != len(set(case_ids))
            or any(not isinstance(case.get("group_keys"), dict) or not case["group_keys"]
                   or not isinstance(case.get("suite"), str) or not case["suite"] for case in cases)):
        raise ValueError("Split cases need unique IDs, suites and group keys")
    components = group_components(cases)
    suites = sorted({case["suite"] for case in cases})
    suite_masks = [{cases[index]["suite"] for index in component} for component in components]
    insufficient = [suite for suite in suites if sum(suite in mask for mask in suite_masks) < 2]
    if insufficient:
        raise ValueError("Suite lacks two independent group components: " + ", ".join(insufficient))

    candidates = []
    count = len(components)
    if count <= 18:
        for size in range(1, count):
            candidates.extend(itertools.combinations(range(count), size))
    else:
        rng = random.Random(0)
        for _ in range(4096):
            candidates.append(tuple(index for index in range(count)
                                    if rng.random() < locked_target))

    best = None
    best_score = None
    all_suites = set(suites)
    for selected in candidates:
        selected_set = set(selected)
        locked_count = sum(len(components[index]) for index in selected_set)
        ratio = locked_count / len(cases)
        if not 0.30 <= ratio <= 0.50:
            continue
        locked_suites = set().union(*(suite_masks[index] for index in selected_set))
        dev_suites = set().union(*(suite_masks[index] for index in range(count)
                                   if index not in selected_set))
        if locked_suites != all_suites or dev_suites != all_suites:
            continue
        score = (abs(ratio - locked_target), tuple(selected))
        if best_score is None or score < best_score:
            best_score, best = score, selected_set
    if best is None:
        raise ValueError("No grouped split candidate met 30–50% locked coverage in every suite")
    return {cases[index]["case_id"]: "locked_candidate" if component_id in best else "dev"
            for component_id, component in enumerate(components) for index in component}


def prepare_candidate(packet: Path, output: Path) -> dict:
    validation = validate_packet(packet)
    if validation["status"] != "forms_complete":
        raise ValueError("Cannot prepare split until independent reviews and adjudication are complete: "
                         + ", ".join(validation.get("errors", [])))
    packet_bytes = (packet / "review_cases.jsonl").read_bytes()
    source_cases = [json.loads(line) for line in packet_bytes.decode("utf-8").splitlines() if line.strip()]
    with (packet / "adjudication.csv").open(encoding="utf-8", newline="") as stream:
        decisions = {row["case_id"]: row for row in csv.DictReader(stream)}
    reviewed = []
    for source in source_cases:
        decision = decisions[source["case_id"]]
        if decision["final_decision"] not in {"accept", "revise"}:
            continue
        case = dict(source)
        if decision["final_decision"] == "revise":
            case["gold"] = json.loads(decision["final_gold_json"])
        case["review_status"] = "forms_complete_unverified"
        reviewed.append(case)
    counts = Counter(case["suite"] for case in reviewed)
    missing = {suite: required - counts[suite] for suite, required in MINIMUM_BY_SUITE.items()
               if counts[suite] < required}
    if missing:
        raise ValueError(f"Reviewed minimum case counts are missing: {missing}")
    assignment = plan_grouped_split(reviewed)
    for case in reviewed:
        case["split"] = assignment[case["case_id"]]
    output.mkdir(parents=True, exist_ok=False)
    case_path = output / "reviewed_cases.jsonl"
    case_path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in reviewed),
                         encoding="utf-8")
    split_counts = Counter(assignment.values())
    manifest = {"status": "reviewed_split_candidate", "locked_release_pass": False,
                "human_independence_verified_by_software": False,
                "source_packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
                "reviewed_cases_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
                "case_count": len(reviewed), "split_counts": dict(split_counts),
                "suite_counts": dict(counts), "created_at": datetime.now(timezone.utc).isoformat(),
                "next_gate": "freeze code, dataset, policy, model, Prompt and scorer before a locked run"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "evals/reviewed_candidates" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
    manifest = prepare_candidate(args.packet, output)
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
