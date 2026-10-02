from __future__ import annotations

import json
import os
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel

from .models_config import ModelRegistry
from .prompts import PromptRegistry

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
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.3)
                continue
            response.raise_for_status()
            return schema.model_validate(json.loads(response.json()["choices"][0]["message"]["content"]))
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == 0:
                continue
            raise ModelUnavailable("OpenRouter transport failure") from exc
        except (KeyError, ValueError, httpx.HTTPStatusError) as exc:
            raise ModelUnavailable("OpenRouter response invalid or rejected") from exc
    raise ModelUnavailable("OpenRouter retry limit exceeded")
