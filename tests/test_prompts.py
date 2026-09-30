from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolveai import prompts


def test_release_hash_and_cloud_independence(tmp_path, monkeypatch):
    source = prompts.ROOT
    (tmp_path / "prompts/catalog").mkdir(parents=True)
    (tmp_path / "prompts/releases").mkdir(parents=True)
    for name in json.loads((source / "prompts/releases/release-v1.json").read_text())["prompts"]:
        (tmp_path / "prompts/catalog" / f"{name}.yaml").write_bytes((source / "prompts/catalog" / f"{name}.yaml").read_bytes())
    (tmp_path / "prompts/releases/release-v1.json").write_bytes((source / "prompts/releases/release-v1.json").read_bytes())
    monkeypatch.setattr(prompts, "ROOT", tmp_path)
    registry = prompts.PromptRegistry("release-v1")
    assert registry.get("coordinator")["release_id"] == "release-v1"
    with (tmp_path / "prompts/catalog/coordinator.yaml").open("a") as handle:
        handle.write("\n# drift\n")
    with pytest.raises(prompts.PromptError, match="hash mismatch"):
        prompts.PromptRegistry("release-v1")
