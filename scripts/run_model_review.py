"""Run a resumable, advisory OpenRouter review of an ignored minimum packet."""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from resolveai import models as domain_models
from resolveai.db import Base, make_engine
from resolveai.openrouter import strict_json_schema
from resolveai.prompts import PromptRegistry, ROOT
from resolveai.seed import seed_demo


MODEL_OPTIONS = {
    "deepseek/deepseek-v3.2": {"input_per_m": 0.2088, "output_per_m": 0.3096,
                                "output_name": "model_review_deepseek_v32", "default_max_cost_usd": 1.00},
    "openai/gpt-6-astra-pro": {"input_per_m": 10.0, "output_per_m": 50.0,
                                "output_name": "model_review", "default_max_cost_usd": 30.0},
}
MODEL = "openai/gpt-6-astra-pro"
PROMPT_RELEASE = "review-v1"
CRITERIA = ROOT / "docs/implementation/eval-review-criteria-v3.md"
PRICE_INPUT_PER_M = 10.0
PRICE_OUTPUT_PER_M = 50.0
SHEET_FIELDS = ["case_id", "suite", "source_path", "source_line", "risk_tier", "reviewer_id", "decision", "notes", "revised_gold_json", "reviewed_at_utc"]
DECISIONS = {"accept", "revise", "reject", "needs_context"}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    severity: Literal["critical", "major", "minor"]
    evidence_path: str
    evidence_quote: str
    explanation: str
    suggested_change: str


class CaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    decision: Literal["accept", "revise", "reject", "needs_context"]
    risk_tier_assessment: Literal["normal", "critical"]
    summary: str
    findings: list[Finding]
    missing_context: list[str]
    confidence_percent: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def consistent(self):
        if self.decision == "accept" and self.findings:
            raise ValueError("Accept cannot contain findings")
        if self.decision in {"revise", "reject"} and not self.findings:
            raise ValueError("Revision or rejection requires a finding")
        if self.decision == "needs_context" and not self.missing_context:
            raise ValueError("Needs-context requires missing facts")
        return self


class ModelResponseError(ValueError):
    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inference_fingerprint() -> str:
    code = "\n".join(inspect.getsource(part) for part in (
        Finding, CaseReview, validate_review, load_material, seed_snapshot,
        with_reference_material, request_review))
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def json_pointer(value: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise ValueError("Evidence must use a JSON pointer")
    current = value
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def validate_review(review: CaseReview, case: dict, material: dict) -> list[dict]:
    if review.case_id != case["case_id"]:
        raise ValueError("Review case ID mismatch")
    audit = []
    for finding in review.findings:
        cited = json_pointer(material, finding.evidence_path)
        rendered = json.dumps(cited, ensure_ascii=False)
        audit.append({"evidence_path": finding.evidence_path,
                      "quote_matches": not finding.evidence_quote or finding.evidence_quote in rendered,
                      "actual_value": rendered[:400]})
    return audit


def load_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if key:
        return key
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "OPENROUTER_API_KEY":
                key = value.strip().strip("\"'")
                if key:
                    return key
    raise RuntimeError("OPENROUTER_API_KEY is unavailable in environment or ignored .env")


def load_material(case: dict) -> dict:
    path = ROOT / case["source_path"]
    if sha256(path) != _source_hashes[case["source_path"]]:
        raise ValueError(f"Source changed since packet creation: {case['source_path']}")
    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[case["source_line"] - 1])
    if raw.get("case_id") != case["case_id"]:
        raise ValueError("Source line no longer matches packet")
    return with_reference_material(case, {"case": case, "source_record": raw})


_source_hashes: dict[str, str] = {}


@lru_cache(maxsize=1)
def seed_snapshot() -> dict[str, dict]:
    """Read fixture facts from the checked-in seeder, never from proposed gold."""
    clock = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    engine = make_engine("sqlite+pysqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed_demo(db, clock)
            db.flush()
            result = {}
            for order in db.scalars(select(domain_models.Order)).all():
                items = db.scalars(select(domain_models.OrderItem).where(domain_models.OrderItem.order_id == order.id)).all()
                shipments = db.scalars(select(domain_models.Shipment).where(domain_models.Shipment.order_id == order.id)).all()
                result[order.id] = {
                    "reference_clock_utc": clock.isoformat(), "order_id": order.id,
                    "customer_id": order.customer_id, "order_status": order.status,
                    "order_version": order.version, "policy_bundle_id": order.policy_bundle_id,
                    "favorable_window_days": order.favorable_window_days,
                    "items": [{"item_id": item.id, "quantity": item.quantity, "paid_cents": item.paid_cents,
                               "physical": db.get(domain_models.Product, item.product_id).physical,
                               "returnable": db.get(domain_models.Product, item.product_id).returnable,
                               "special_notice_accepted": db.get(domain_models.Product, item.product_id).special_notice_accepted}
                              for item in items],
                    "shipments": [{"status": shipment.status,
                                   "delivered_at_utc": shipment.delivered_at.replace(tzinfo=timezone.utc).isoformat() if shipment.delivered_at else None}
                                  for shipment in shipments],
                }
            return result
    finally:
        engine.dispose()


def with_reference_material(case: dict, material: dict) -> dict:
    fixture = case["input"].get("fixture") or {}
    order_id = fixture.get("order_id")
    if not order_id:
        return material
    facts = seed_snapshot().get(order_id)
    if facts:
        facts = json.loads(json.dumps(facts))
        if case["input"].get("terminal_scenario") == "expired_window":
            facts["shipments"][0]["delivered_at_utc"] = "20 days before runner invocation"
        material["seed_facts"] = facts
    return material


def request_review(client: httpx.Client, key: str, content: str, case: dict, material: dict, max_tokens: int) -> tuple[CaseReview, dict, list[dict]]:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": content}],
        "provider": {"require_parameters": True},
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "EvalCaseReviewV1", "strict": True, "schema": strict_json_schema(CaseReview)}},
    }
    for attempt in range(3):
        try:
            response = client.post("https://openrouter.ai/api/v1/chat/completions", json=body,
                                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            message = payload["choices"][0]["message"]
            usage = payload.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            reported_cost = usage.get("cost")
            if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
                raise ValueError("OpenRouter response omitted token usage")
            if not isinstance(reported_cost, (int, float)) or reported_cost < 0:
                reported_cost = (input_tokens * PRICE_INPUT_PER_M + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000
            meter = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                     "cost_usd": round(float(reported_cost), 8), "cost_source": "provider" if isinstance(usage.get("cost"), (int, float)) else "list_price_estimate",
                     "provider": payload.get("provider"), "finish_reason": payload["choices"][0].get("finish_reason")}
            try:
                review = CaseReview.model_validate_json(message["content"])
                audit = validate_review(review, case, material)
            except (ValueError, KeyError, TypeError) as exc:
                raise ModelResponseError(f"Invalid structured review: {type(exc).__name__}: {str(exc)[:150]}", meter) from exc
            return review, meter, audit
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("OpenRouter retry limit exceeded")


def compare_human(packet: Path, cases: list[dict], results: dict[str, dict], out: Path) -> int:
    with (packet / "reviewer_a.csv").open(encoding="utf-8", newline="") as stream:
        human = {row["case_id"]: row for row in csv.DictReader(stream)}
    rows = []
    for case in cases:
        case_id = case["case_id"]
        model = results.get(case_id)
        row = human.get(case_id, {})
        decision = row.get("decision", "").strip()
        if not model or decision not in DECISIONS:
            continue
        review = model["review"]
        if decision != review["decision"] or case["risk_tier"] != review["risk_tier_assessment"]:
            rows.append({"case_id": case_id, "suite": case["suite"], "human_decision": decision,
                         "model_decision": review["decision"], "assigned_risk_tier": case["risk_tier"],
                         "model_risk_tier": review["risk_tier_assessment"], "model_summary": review["summary"]})
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", "suite", "human_decision", "model_decision", "assigned_risk_tier", "model_risk_tier", "model_summary"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export(packet: Path, cases: list[dict], results: dict[str, dict], output: Path) -> dict:
    rows = []
    for case in cases:
        item = results.get(case["case_id"])
        if not item:
            continue
        review = item["review"]
        mismatches = [audit["evidence_path"] for audit in item.get("evidence_audit", []) if not audit["quote_matches"]]
        rows.append({"case_id": case["case_id"], "suite": case["suite"], "source_path": case["source_path"],
                     "source_line": case["source_line"], "risk_tier": case["risk_tier"],
                     "reviewer_id": f"ai:openrouter:{MODEL}", "decision": review["decision"],
                     "notes": review["summary"] + " " + "; ".join(f["evidence_path"] + ": " + f["explanation"] + " Suggested: " + f["suggested_change"] for f in review["findings"])
                              + (" Quote mismatch at " + ", ".join(mismatches) if mismatches else ""),
                     "revised_gold_json": "", "reviewed_at_utc": item["reviewed_at_utc"]})
    with (output / "model_review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    disagreement_count = compare_human(packet, cases, results, output / "human_model_disagreements.csv")
    counts = {decision: sum(item["review"]["decision"] == decision for item in results.values()) for decision in DECISIONS}
    usage = {"input_tokens": sum(item["usage"]["input_tokens"] for item in results.values()),
             "output_tokens": sum(item["usage"]["output_tokens"] for item in results.values()),
             "cost_usd": round(sum(item["usage"]["cost_usd"] for item in results.values()), 8)}
    return {"reviewed": len(results), "pending": len(cases) - len(results), "decisions": counts,
            "human_model_disagreements": disagreement_count, "usage": usage,
            "evidence_quote_mismatches": sum(not audit["quote_matches"] for item in results.values() for audit in item.get("evidence_audit", [])),
            "human_release_review_complete": False, "locked_release_ready": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="Ignored packet created by prepare_minimum_review.py")
    parser.add_argument("--model", choices=tuple(MODEL_OPTIONS), default="deepseek/deepseek-v3.2",
                        help="Pinned reviewer model; each model writes to a separate output directory")
    parser.add_argument("--limit", type=int, default=0, help="Max new cases this invocation; zero means all")
    parser.add_argument("--case-id", action="append", default=[], help="Review only selected IDs, repeatable")
    parser.add_argument("--refresh-only", action="store_true", help="Rebuild comparison outputs from saved reviews without an API call or key")
    parser.add_argument("--max-cost-usd", type=float, help="Stop before next request when cumulative spend reaches this cap")
    parser.add_argument("--max-tokens", type=int, default=3000)
    args = parser.parse_args()
    global MODEL, PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M
    MODEL = args.model
    choice = MODEL_OPTIONS[MODEL]
    PRICE_INPUT_PER_M = choice["input_per_m"]
    PRICE_OUTPUT_PER_M = choice["output_per_m"]
    if args.max_cost_usd is None:
        args.max_cost_usd = choice["default_max_cost_usd"]
    if args.limit < 0 or args.max_cost_usd <= 0 or args.max_tokens <= 0:
        parser.error("limit, cost cap and max tokens must be valid positive bounds")
    packet = args.packet.resolve()
    packet_manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    packet_path = packet / "review_cases.jsonl"
    cases = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines()]
    if len(cases) != packet_manifest["case_count"] or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Packet case count or IDs invalid")
    global _source_hashes
    _source_hashes = {source["path"]: source["sha256"] for source in packet_manifest["source_datasets"]}
    prompt = PromptRegistry(PROMPT_RELEASE).get("eval_case_review")
    criteria_hash = sha256(CRITERIA)
    output = packet / choice["output_name"]
    output.mkdir(exist_ok=True)
    result_dir = output / "cases"
    result_dir.mkdir(exist_ok=True)
    identity = {"packet_sha256": sha256(packet_path), "criteria_sha256": criteria_hash,
                "prompt_release_id": PROMPT_RELEASE, "prompt_sha256": prompt["sha256"], "model": MODEL,
                "list_price_input_per_m": PRICE_INPUT_PER_M, "list_price_output_per_m": PRICE_OUTPUT_PER_M,
                "seed_code_sha256": sha256(ROOT / "apps/api/resolveai/seed.py"),
                "core_runner_sha256": sha256(ROOT / "evals/runners/run_core_business.py"),
                "business_runner_sha256": sha256(ROOT / "evals/runners/run_business.py"),
                "schema_sha256": hashlib.sha256(json.dumps(strict_json_schema(CaseReview), sort_keys=True).encode()).hexdigest()}
    manifest_path = output / "manifest.json"
    previous = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(previous.get(key) != value for key, value in identity.items()
               if key in previous or not key.startswith("list_price_")):
            raise ValueError("Existing model review belongs to a different packet, prompt, model or schema")
        if previous.get("inference_sha256") and previous["inference_sha256"] != inference_fingerprint():
            raise ValueError("Review inference implementation changed; use a new packet")
        if not previous.get("inference_sha256") and previous.get("reviewer_code_sha256") != sha256(Path(__file__)) and not args.refresh_only:
            raise ValueError("Presentation code changed; run --refresh-only once before resuming")
    results = {}
    for case in cases:
        case_id = case["case_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id):
            raise ValueError("Unsafe case ID")
        path = result_dir / f"{case_id}.json"
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            review = CaseReview.model_validate(item["review"])
            validate_review(review, case, load_material(case))
            results[case_id] = item
    failed_usage = []
    failed_path = output / "failed_calls.jsonl"
    if failed_path.exists():
        failed_usage = [json.loads(line) for line in failed_path.read_text(encoding="utf-8").splitlines() if line]
    def save():
        summary = export(packet, cases, results, output)
        summary["failed_calls"] = len(failed_usage)
        summary["usage"]["failed_call_cost_usd"] = round(sum(row["usage"]["cost_usd"] for row in failed_usage), 8)
        summary["usage"]["total_cost_usd"] = round(summary["usage"]["cost_usd"] + summary["usage"]["failed_call_cost_usd"], 8)
        blocker_path = output / "blocker.json"
        if blocker_path.exists():
            summary["blocker"] = json.loads(blocker_path.read_text(encoding="utf-8"))
        manifest = {**identity, "reviewer_code_sha256": previous.get("reviewer_code_sha256", sha256(Path(__file__))),
                    "inference_sha256": inference_fingerprint(), "current_code_sha256": sha256(Path(__file__)),
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "reviewer_type": "advisory_ai", "case_count": len(cases), **summary}
        temp = manifest_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(manifest_path)
        return manifest
    save()
    if args.refresh_only:
        summary = save()
        print(json.dumps({"output": str(output), "reviewed": summary["reviewed"], "pending": summary["pending"],
                          "human_model_disagreements": summary["human_model_disagreements"], "blocker": summary.get("blocker")}, ensure_ascii=False))
        return 0
    selected = set(args.case_id)
    if selected - {case["case_id"] for case in cases}:
        raise ValueError("Unknown selected case ID")
    key = load_key()
    completed = 0
    with httpx.Client(timeout=180) as client:
        for case in cases:
            case_id = case["case_id"]
            if case_id in results or (selected and case_id not in selected):
                continue
            if args.limit and completed >= args.limit:
                break
            spent = sum(item["usage"]["cost_usd"] for item in results.values()) + sum(row["usage"]["cost_usd"] for row in failed_usage)
            if spent >= args.max_cost_usd:
                break
            material = load_material(case)
            content = prompt["template"].format(review_criteria=CRITERIA.read_text(encoding="utf-8"),
                                                review_material=json.dumps(material, ensure_ascii=False, sort_keys=True))
            try:
                review, usage, audit = request_review(client, key, content, case, material, args.max_tokens)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, ModelResponseError):
                    failure = {"case_id": case_id, "at_utc": datetime.now(timezone.utc).isoformat(), "usage": exc.usage}
                    with failed_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(failure) + "\n")
                    failed_usage.append(failure)
                (output / "errors.log").open("a", encoding="utf-8").write(
                    f"{datetime.now(timezone.utc).isoformat()} {case_id} {type(exc).__name__}: {str(exc)[:240]}\n")
                if isinstance(exc, httpx.HTTPStatusError):
                    status_code = exc.response.status_code
                    try:
                        provider_message = str((exc.response.json().get("error") or {}).get("message", ""))
                    except (ValueError, AttributeError):
                        provider_message = ""
                    blocker_kind = ("provider_payment_required" if status_code == 402 else
                                    "provider_key_limit_exceeded" if status_code == 403 and "Key limit exceeded" in provider_message else None)
                    if blocker_kind:
                        (output / "blocker.json").write_text(json.dumps({"kind": blocker_kind,
                            "http_status": status_code, "case_id": case_id,
                            "observed_at_utc": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n", encoding="utf-8")
                save()
                print(json.dumps({"case_id": case_id, "error_type": type(exc).__name__, "reviewed": len(results)}, ensure_ascii=False), file=sys.stderr)
                return 1
            item = {"review": review.model_dump(), "usage": usage, "evidence_audit": audit,
                    "reviewed_at_utc": datetime.now(timezone.utc).isoformat()}
            path = result_dir / f"{case_id}.json"
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(path)
            results[case_id] = item
            (output / "blocker.json").unlink(missing_ok=True)
            completed += 1
            summary = save()
            print(json.dumps({"case_id": case_id, "decision": review.decision,
                              "reviewed": summary["reviewed"], "cost_usd": summary["usage"]["total_cost_usd"]}, ensure_ascii=False), flush=True)
    summary = save()
    print(json.dumps({"output": str(output), "reviewed": summary["reviewed"], "pending": summary["pending"],
                      "cost_usd": summary["usage"]["total_cost_usd"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
