from __future__ import annotations

import httpx
import anyio

from evals.runners.run_paired_model import ProviderUsage


def test_provider_usage_records_only_numeric_openrouter_metadata(monkeypatch):
    def post(_client, url, **_kwargs):
        if str(url).startswith("https://openrouter.ai"):
            return httpx.Response(200, request=httpx.Request("POST", url), json={"usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.0000042}})
        return httpx.Response(200, request=httpx.Request("POST", url), json={"usage": {"prompt_tokens": 999}})

    monkeypatch.setattr(httpx.Client, "post", post)
    with ProviderUsage() as meter:
        with httpx.Client() as client:
            client.post("https://openrouter.ai/api/v1/chat/completions")
            client.post("https://example.test/local")
    assert meter.summary() == {"calls": 1, "input_tokens": 12, "output_tokens": 4, "reported_cost_usd": 0.0000042, "cost_missing_calls": 0, "http_errors": 0}
    assert httpx.Client.post is post


def test_provider_usage_records_async_transport_and_restores_it(monkeypatch):
    async def post(_client, url, **_kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.0000042}})

    async def call():
        async with httpx.AsyncClient() as client:
            await client.post("https://openrouter.ai/api/v1/chat/completions")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with ProviderUsage() as meter:
        anyio.run(call)
    assert meter.summary()["calls"] == 1
    assert meter.summary()["reported_cost_usd"] == 0.0000042
    assert httpx.AsyncClient.post is post
