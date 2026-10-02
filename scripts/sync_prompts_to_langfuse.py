"""One-way local prompt mirror. Never feeds Cloud text into runtime inference."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from resolveai.prompts import PromptRegistry, ROOT


def client():
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        raise RuntimeError("Langfuse keys are not configured")
    from langfuse import Langfuse
    return Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"], base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), tracing_enabled=False)


def find_version(lf, name: str, digest: str, template: str):
    listing = lf.api.prompts.list(name=name, limit=100)
    for meta in listing.data:
        if meta.name != name:
            continue
        for version in meta.versions:
            remote = lf.api.prompts.get(name, version=version)
            if isinstance(remote.config, dict) and remote.config.get("prompt_sha256") == digest and remote.prompt == template:
                return version
    return None


def sync(release_id: str, check_only: bool) -> dict:
    registry = PromptRegistry(release_id)
    lf = client()
    mapping = {}
    drift = []
    for name in registry.manifest["prompts"]:
        local = registry.get(name)
        digest = local["sha256"]
        template = local["template"]
        version = find_version(lf, name, digest, template)
        if version is None and not check_only:
            created = lf.create_prompt(name=name, type=local["type"], prompt=template, config={"prompt_sha256": digest, "release_id": release_id, "source_git_commit": registry.manifest.get("source_git_commit"), "schema_ref": local["schema_ref"]}, labels=[])
            version = created.version
        if version is None:
            drift.append(name + ": missing mirror")
            continue
        mapping[name] = version
        try:
            labeled = lf.api.prompts.get(name, label="resolveai-release")
            label_matches = labeled.version == version and labeled.prompt == template
        except Exception:
            label_matches = False
        if not label_matches:
            if check_only:
                drift.append(name + ": release label drift")
            else:
                lf.update_prompt(name=name, version=version, new_labels=["resolveai-release"])
    result = {"release_id": release_id, "mapping": mapping, "drift": drift, "status": "ok" if not drift else "drift"}
    output = ROOT / "observability" / "prompt_mirror_map.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("release_id")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        result = sync(args.release_id, args.check)
    except Exception as exc:
        result = {"release_id": args.release_id, "status": "incomplete", "error": str(exc)}
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "ok" else 1)
