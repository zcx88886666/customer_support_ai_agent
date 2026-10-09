"""Queue one fixed no-key development evaluation on the private bulk worker."""

from __future__ import annotations

import argparse
import json
import os

from resolveai.evaluation_jobs import SUITES
from resolveai.jobs import app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES)
    args = parser.parse_args()
    namespace = os.environ.get("JOB_NAMESPACE", "dev")
    result = app.tasks["resolveai.jobs.development_eval"].apply_async(
        args=(namespace, args.suite))
    print(json.dumps({"task_id": result.id, "suite": args.suite, "status": "queued",
                      "report": f"evals/reports/queued/{result.id}/summary.json"}))


if __name__ == "__main__":
    main()
