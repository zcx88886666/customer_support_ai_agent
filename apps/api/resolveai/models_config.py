from __future__ import annotations

import json
import hashlib
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
    max_input_token_bound: int
    max_call_cost_usd: float
    input_price_per_million: float
    output_price_per_million: float
    technical_attempts: int
    allow_provider_fallbacks: bool


class ModelRegistry:
    def __init__(self, version: str = "mock-v1"):
        path = ROOT / "packages/agent" / f"models-{version}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != version:
            raise ValueError("Model registry version mismatch")
        raw = (ROOT / "packages/agent/resources-v1.json").read_bytes()
        resources = json.loads(raw)
        if resources["version"] != "v1" or resources["technical_attempts"] not in (1, 2):
            raise ValueError("Invalid model resource policy")
        self.resource_policy_sha256 = hashlib.sha256(raw).hexdigest()
        self.tasks = {}
        for name, value in data["tasks"].items():
            prices = resources["model_prices_per_million"][value["model"]]
            self.tasks[name] = ModelTask(
                model=value["model"], prompt_name=value["prompt_name"], timeout_seconds=value["timeout_seconds"],
                max_tokens=value["max_tokens"], temperature=value["temperature"], provider_order=tuple(value["provider_order"]),
                max_input_token_bound=resources["max_input_token_bound"], max_call_cost_usd=resources["max_call_cost_usd"],
                input_price_per_million=prices["input"], output_price_per_million=prices["output"],
                technical_attempts=resources["technical_attempts"], allow_provider_fallbacks=resources["allow_provider_fallbacks"])

    def get(self, task: str) -> ModelTask:
        return self.tasks[task]
