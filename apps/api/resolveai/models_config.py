from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .prompts import ROOT


@dataclass(frozen=True)
class ModelTask:
    model: str
    prompt_name: str
    timeout_seconds: float
    max_tokens: int
    temperature: float
    provider_order: tuple[str, ...]


class ModelRegistry:
    def __init__(self, version: str = "mock-v1"):
        path = ROOT / "packages/agent" / f"models-{version}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != version:
            raise ValueError("Model registry version mismatch")
        self.tasks = {name: ModelTask(model=value["model"], prompt_name=value["prompt_name"], timeout_seconds=value["timeout_seconds"], max_tokens=value["max_tokens"], temperature=value["temperature"], provider_order=tuple(value["provider_order"])) for name, value in data["tasks"].items()}

    def get(self, task: str) -> ModelTask:
        return self.tasks[task]
