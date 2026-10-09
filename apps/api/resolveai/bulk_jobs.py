"""Publish a validated synthetic world from a fixed, named generation contract."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from data.generator.generate import PROFILES, generate
from data.generator.validate import validate


def generate_world(root: Path, output_name: str, profile: str, seed: int,
                   clock: str) -> dict[str, str | int]:
    if not isinstance(output_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", output_name):
        raise ValueError("Invalid bulk output name")
    if profile not in PROFILES or type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid bulk generation profile or seed")
    try:
        parsed_clock = datetime.fromisoformat(clock)
    except (TypeError, ValueError) as exc:
        raise ValueError("Bulk generation clock must be ISO-8601 with a timezone") from exc
    if parsed_clock.tzinfo is None or parsed_clock.utcoffset() is None:
        raise ValueError("Bulk generation clock must be ISO-8601 with a timezone")

    root = Path(root)
    destination = root / output_name

    def existing_result() -> dict[str, str | int]:
        if destination.is_symlink():
            raise ValueError("Bulk output cannot be a symlink")
        report_path = destination / "data_quality_report.json"
        if not destination.is_dir() or not report_path.is_file():
            raise ValueError("Existing bulk output is incomplete")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Existing bulk output is incomplete") from exc
        if (report.get("seed") != seed or report.get("clock") != parsed_clock.isoformat()
                or report.get("orders_requested") != PROFILES[profile]):
            raise ValueError("Existing bulk output has a different generation contract")
        quality = validate(destination)
        if quality["violation_count"] or not quality["file_hashes_match"]:
            raise ValueError("Existing bulk output failed validation")
        return {"profile": profile, "orders_checked": quality["orders_checked"],
                "violation_count": quality["violation_count"], "output_name": output_name}

    if destination.exists() or destination.is_symlink():
        return existing_result()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".stage-{output_name}-", dir=root) as temporary:
        staged = Path(temporary) / "world"
        generate(staged, PROFILES[profile], seed, parsed_clock)
        quality = validate(staged)
        if quality["violation_count"] or not quality["file_hashes_match"]:
            raise ValueError("Generated bulk output failed validation")
        try:
            staged.rename(destination)
        except OSError:
            if destination.exists() or destination.is_symlink():
                return existing_result()
            raise
    return {"profile": profile, "orders_checked": quality["orders_checked"],
            "violation_count": quality["violation_count"], "output_name": output_name}
