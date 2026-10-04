from __future__ import annotations

import json
import hashlib
import math
import os
import time
from typing import TypeVar

import httpx
import anyio
from pydantic import BaseModel

from .models_config import ModelRegistry
from .prompts import PromptRegistry, ROOT
from .telemetry import tracer
from .request_budget import BudgetExceeded, RequestBudget, current_budget, remaining_io_seconds

T = TypeVar("T", bound=BaseModel)
CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


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


async def post_with_deadline(body: dict, key: str, timeout: float) -> httpx.Response:
    # HTTPX timeouts apply separately to individual I/O operations. This scope
    # also cancels a peer that trickles response bytes indefinitely.
    with anyio.fail_after(timeout):
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(CHAT_COMPLETIONS_URL, json=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})


def call_structured(task: str, variables: dict[str, str], schema: type[T], release_id: str = "release-v1") -> T:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise ModelUnavailable("OpenRouter key missing")
    registry = ModelRegistry()
    config = registry.get(task)
    prompt = PromptRegistry(release_id).get(config.prompt_name)
    content = prompt["template"].format(**variables)
    output_schema = strict_json_schema(schema)
    body = {"model": config.model, "messages": [{"role": "user", "content": content}], "temperature": config.temperature, "max_tokens": config.max_tokens, "provider": {"order": list(config.provider_order), "require_parameters": True, "allow_fallbacks": config.allow_provider_fallbacks}, "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": output_schema}}}
    budget = current_budget() or RequestBudget()
    # Conservative byte-based admission estimate, including schema and framing;
    # reconcile with actual provider usage, retaining reservations if unknown.
    input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 256
    cost_bound = (input_bound * config.input_price_per_million + config.max_tokens * config.output_price_per_million) / 1_000_000
    if input_bound > config.max_input_token_bound or cost_bound > config.max_call_cost_usd:
        budget.stop("model_call_limit")
        raise ModelUnavailable("Model call resource limit exceeded")
    schema_hash = hashlib.sha256(json.dumps(output_schema, sort_keys=True).encode()).hexdigest()
    for attempt in range(config.technical_attempts):
        try:
            timeout = min(config.timeout_seconds, budget.remaining_seconds(), remaining_io_seconds(config.timeout_seconds))
            ticket = budget.reserve(input_bound, config.max_tokens, cost_bound)
        except BudgetExceeded as exc:
            raise ModelUnavailable("Request resource limit exceeded") from exc
        usage = {}
        with tracer().start_as_current_span("llm." + task, record_exception=False, set_status_on_exception=False) as span:
            span.set_attributes({"langfuse.observation.type": "generation", "gen_ai.request.model": config.model, "gen_ai.system": "openrouter", "prompt_release_id": release_id, "prompt_sha256": prompt["sha256"], "model_attempt": attempt + 1, "request_llm_attempt": ticket, "schema_sha256": schema_hash, "resource_policy_sha256": registry.resource_policy_sha256})
            # A mirror is optional. Inference always uses the local verified Prompt.
            try:
                mirror = json.loads((ROOT / "observability/prompt_mirror_map.json").read_text())
                version = mirror.get("mapping", {}).get(config.prompt_name)
                if mirror.get("status") == "ok" and mirror.get("release_id") == release_id and isinstance(version, int):
                    span.set_attributes({"langfuse.observation.prompt.name": config.prompt_name, "langfuse.observation.prompt.version": version})
            except (OSError, ValueError, AttributeError):
                pass
            try:
                response = anyio.run(post_with_deadline, body, key, timeout)
                span.set_attribute("http.response.status_code", response.status_code)
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
                    usage = payload["usage"]
                if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < config.technical_attempts:
                    span.set_attribute("langfuse.observation.level", "WARNING")
                    time.sleep(min(0.3, budget.remaining_seconds()))
                    continue
                response.raise_for_status()
                if not isinstance(payload, dict):
                    raise ValueError("Model response must be an object")
                for source, target in (("prompt_tokens", "gen_ai.usage.input_tokens"), ("completion_tokens", "gen_ai.usage.output_tokens")):
                    count = usage.get(source)
                    if type(count) is int and count >= 0:
                        span.set_attribute(target, count)
                cost = usage.get("cost")
                if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                    span.set_attribute("langfuse.observation.cost_details", json.dumps({"total": cost}))
                budget.finish(ticket, usage)
                ticket = None
                budget.remaining_seconds()
                remaining_io_seconds(config.timeout_seconds)
                return schema.model_validate(json.loads(payload["choices"][0]["message"]["content"]))
            except (TimeoutError, httpx.TransportError) as exc:
                span.set_attributes({"langfuse.observation.level": "ERROR", "error.type": "transport_failure"})
                if attempt + 1 < config.technical_attempts:
                    continue
                raise ModelUnavailable("OpenRouter transport failure") from exc
            except BudgetExceeded as exc:
                raise ModelUnavailable("Request resource limit exceeded") from exc
            except (KeyError, ValueError, TypeError, IndexError, httpx.HTTPStatusError) as exc:
                span.set_attributes({"langfuse.observation.level": "ERROR", "error.type": "invalid_response"})
                raise ModelUnavailable("OpenRouter response invalid or rejected") from exc
            finally:
                if ticket is not None:
                    budget.finish(ticket, usage)
    raise ModelUnavailable("OpenRouter retry limit exceeded")
