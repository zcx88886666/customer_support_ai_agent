from __future__ import annotations

from fastapi.testclient import TestClient

from services.commerce_mcp.server import app


def test_mcp_rejects_missing_bearer_token():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
