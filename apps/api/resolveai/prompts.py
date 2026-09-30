from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


class PromptError(RuntimeError):
    pass


class PromptRegistry:
    def __init__(self, release_id: str):
        self.release_id = release_id
        path = ROOT / "prompts" / "releases" / f"{release_id}.json"
        if not path.is_file():
            raise PromptError(f"Prompt release missing: {release_id}")
        self.manifest = json.loads(path.read_text(encoding="utf-8"))
        if self.manifest.get("release_id") != release_id:
            raise PromptError("Release ID mismatch")
        if not self.manifest.get("verified") or not self.manifest.get("source_git_commit"):
            raise PromptError("Only committed, verified prompt releases may run")
        self._prompts = {}
        for name, digest in self.manifest.get("prompts", {}).items():
            source = ROOT / "prompts" / "catalog" / f"{name}.yaml"
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise PromptError(f"Prompt hash mismatch: {name}")
            value = yaml.safe_load(raw)
            if value.get("name") != name or value.get("type") not in ("text", "chat"):
                raise PromptError(f"Invalid prompt: {name}")
            if not isinstance(value.get("required_variables"), list) or not value.get("schema_ref"):
                raise PromptError(f"Invalid prompt contract: {name}")
            for variable in value["required_variables"]:
                if "{" + variable + "}" not in value.get("template", ""):
                    raise PromptError(f"Missing variable: {variable}")
            self._prompts[name] = value

    def get(self, name: str) -> dict:
        if name not in self._prompts:
            raise PromptError(f"Prompt unavailable: {name}")
        return {**self._prompts[name], "sha256": self.manifest["prompts"][name], "release_id": self.release_id}
