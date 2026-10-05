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
    def __init__(self, message: str, *, tool_calls: int = 0):
        super().__init__(message)
        self.tool_calls = tool_calls


async def call_read_tools(token: str, calls: list[tuple[str, dict]], url: str | None = None) -> list:
    if not token or not 1 <= len(calls) <= 2:
        raise CommerceUnavailable("Authenticated bounded tool calls required")
    if any(name not in {"get_order", "track_shipment", "check_return_eligibility"} for name, _ in calls):
        raise CommerceUnavailable("Tool not allowed")
    results = []
    attempts = 0
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
                                attempts += 1
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
            raise CommerceUnavailable("Commerce query unavailable", tool_calls=attempts) from None
    return results


def read_order(token: str, order_id: str) -> tuple[dict, list[dict]]:
    selected = read_selected_order_tools(token, order_id, ["get_order", "track_shipment"])
    return selected["get_order"], selected["track_shipment"]


def read_selected_order_tools(token: str, order_id: str, tools: list[str]) -> dict:
    if not 1 <= len(tools) <= 2 or len(tools) != len(set(tools)) or any(name not in {"get_order", "track_shipment"} for name in tools):
        raise CommerceUnavailable("Invalid order read plan")
    values = anyio.run(call_read_tools, token, [(name, {"order_id": order_id}) for name in tools])
    if len(values) != len(tools):
        raise CommerceUnavailable("Invalid commerce evidence", tool_calls=len(tools))
    selected = dict(zip(tools, values))
    order = selected.get("get_order")
    if "get_order" in selected and (not isinstance(order, dict) or order.get("id") != order_id):
        raise CommerceUnavailable("Invalid commerce evidence", tool_calls=len(tools))
    shipments = selected.get("track_shipment")
    if "track_shipment" in selected and (not isinstance(shipments, list) or any(not isinstance(row, dict) for row in shipments)):
        raise CommerceUnavailable("Invalid commerce evidence", tool_calls=len(tools))
    return selected
