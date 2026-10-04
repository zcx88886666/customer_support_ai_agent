"""Read-only MCP client. Credentials stay in request-local runtime closures."""

from __future__ import annotations

import json

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from opentelemetry.propagate import inject

from .config import settings
from .telemetry import tracer
from .request_budget import remaining_io_seconds


class CommerceUnavailable(RuntimeError):
    pass


async def call_read_tools(token: str, calls: list[tuple[str, dict]], url: str | None = None) -> list:
    if not token or not 1 <= len(calls) <= 2:
        raise CommerceUnavailable("Authenticated bounded tool calls required")
    if any(name not in {"get_order", "track_shipment", "check_return_eligibility"} for name, _ in calls):
        raise CommerceUnavailable("Tool not allowed")
    results = []
    with tracer().start_as_current_span("commerce.mcp", record_exception=False, set_status_on_exception=False) as span:
        span.set_attribute("tool_calls", len(calls))
        headers = {"Authorization": "Bearer " + token}
        inject(headers)
        try:
            with anyio.fail_after(remaining_io_seconds(10)):
                async with create_mcp_http_client(headers=headers) as client:
                    async with streamable_http_client(url or settings.commerce_mcp_url, http_client=client) as streams:
                        async with ClientSession(*streams) as session:
                            await session.initialize()
                            for name, arguments in calls:
                                result = await session.call_tool(name, arguments, read_timeout_seconds=8)
                                if result.is_error:
                                    raise CommerceUnavailable("Commerce tool refused request")
                                value = result.structured_content
                                if value is None:
                                    value = json.loads(next(block.text for block in result.content if block.type == "text"))
                                if isinstance(value, dict) and set(value) == {"result"}:
                                    value = value["result"]
                                results.append(value)
        except Exception:
            span.set_attribute("error.type", "commerce_unavailable")
            raise CommerceUnavailable("Commerce query unavailable") from None
    return results


def read_order(token: str, order_id: str) -> tuple[dict, list[dict]]:
    order, shipments = anyio.run(call_read_tools, token, [("get_order", {"order_id": order_id}), ("track_shipment", {"order_id": order_id})])
    if not isinstance(order, dict) or order.get("id") != order_id or not isinstance(shipments, list):
        raise CommerceUnavailable("Invalid commerce evidence")
    return order, shipments
