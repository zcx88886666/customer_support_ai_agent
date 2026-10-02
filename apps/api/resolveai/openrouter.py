from __future__ import annotations

import json
import math
import os
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel

from .models_config import ModelRegistry
from .prompts import PromptRegistry, ROOT
from .telemetry import tracer

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def strict_json_schema(model: type[BaseModel]) -> dict:
    """Adapt Pydantic's schema to the strict structured-output subset."""
    def normalize(value):
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: normalize(item) for key, item in value.items() if key != "default"}
        if result.get("type") == "object" and "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        return result

    return normalize(model.model_json_schema())


def call_structured(task: str, variables: dict[str, str], schema: type[T], release_id: str = "release-v1") -> T:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ModelUnavailable("OpenRouter key missing")
    config = ModelRegistry().get(task)
    prompt = PromptRegistry(release_id).get(config.prompt_name)
    content = prompt["template"].format(**variables)
    body = {"model": config.model, "messages": [{"role": "user", "content": content}], "temperature": config.temperature, "max_tokens": config.max_tokens, "provider": {"order": list(config.provider_order), "require_parameters": True}, "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_json_schema(schema)}}}
    for attempt in range(2):
        with tracer().start_as_current_span("llm." + task, record_exception=False, set_status_on_exception=False) as span:
            span.set_attributes({"langfuse.observation.type": "generation", "gen_ai.request.model": config.model, "gen_ai.system": "openrouter", "prompt_release_id": release_id, "prompt_sha256": prompt["sha256"], "model_attempt": attempt + 1})
            # A mirror is optional. Inference always uses the local verified Prompt.
            try:
                mirror = json.loads((ROOT / "observability/prompt_mirror_map.json").read_text())
                version = mirror.get("mapping", {}).get(config.prompt_name)
                if mirror.get("status") == "ok" and mirror.get("release_id") == release_id and isinstance(version, int):
                    span.set_attributes({"langfuse.observation.prompt.name": config.prompt_name, "langfuse.observation.prompt.version": version})
            except (OSError, ValueError, AttributeError):
                pass
            try:
                with httpx.Client(timeout=config.timeout_seconds) as client:
                    response = client.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                span.set_attribute("http.response.status_code", response.status_code)
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    span.set_attribute("langfuse.observation.level", "WARNING")
                    time.sleep(0.3)
                    continue
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                for source, target in (("prompt_tokens", "gen_ai.usage.input_tokens"), ("completion_tokens", "gen_ai.usage.output_tokens")):
                    count = usage.get(source)
                    if isinstance(count, int) and count >= 0:
                        span.set_attribute(target, count)
                cost = usage.get("cost")
                if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0:
                    span.set_attribute("langfuse.observation.cost_details", json.dumps({"total": cost}))
                return schema.model_validate(json.loads(payload["choices"][0]["message"]["content"]))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                span.set_attributes({"langfuse.observation.level": "ERROR", "error.type": "transport_failure"})
                if attempt == 0:
                    continue
                raise ModelUnavailable("OpenRouter transport failure") from exc
            except (KeyError, ValueError, httpx.HTTPStatusError) as exc:
                span.set_attributes({"langfuse.observation.level": "ERROR", "error.type": "invalid_response"})
                raise ModelUnavailable("OpenRouter response invalid or rejected") from exc
    raise ModelUnavailable("OpenRouter retry limit exceeded")
