from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("release_id")
    args = parser.parse_args()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        status = subprocess.check_output(["git", "status", "--porcelain", "--", "prompts/catalog"], cwd=ROOT, text=True)
        if status:
            raise RuntimeError("Catalog must be clean and committed")
        for path in sorted((ROOT / "prompts/catalog").glob("*.yaml")):
            committed = subprocess.check_output(["git", "show", f"{commit}:prompts/catalog/{path.name}"], cwd=ROOT, stderr=subprocess.DEVNULL)
            if committed != path.read_bytes():
                raise RuntimeError("Catalog differs from the commit")
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("A committed catalog is required for a release")
    prompts = {path.stem: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT / "prompts/catalog").glob("*.yaml"))}
    target = ROOT / "prompts/releases" / f"{args.release_id}.json"
    if target.exists():
        raise SystemExit("Release already exists; choose a new ID")
    target.write_text(json.dumps({"release_id": args.release_id, "source_git_commit": commit, "verified": True, "schema_version": "v1", "model_registry_version": "mock-v1", "created_at": datetime.now(timezone.utc).isoformat(), "prompts": prompts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
