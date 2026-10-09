"""Queue one validated, synthetic data profile on the private Celery worker."""

from __future__ import annotations

import argparse
import json
import os
import re

from data.generator.generate import PROFILES
from resolveai.jobs import app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--clock", default="2026-09-29T12:00:00+00:00")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.output_name):
        parser.error("output name must contain only letters, digits, underscores or hyphens")
    namespace = os.environ.get("JOB_NAMESPACE", "dev")
    result = app.tasks["resolveai.jobs.generate_world"].apply_async(
        args=(namespace, args.output_name, args.profile, args.seed, args.clock))
    print(json.dumps({"task_id": result.id, "output_name": args.output_name,
                      "profile": args.profile, "status": "queued"}))


if __name__ == "__main__":
    main()
