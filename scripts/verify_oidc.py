"""Local Keycloak code+PKCE and protected API/MCP integration checks."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import secrets
from functools import partial
from urllib.parse import parse_qs, urlparse

import anyio
import httpx

from resolveai.commerce_client import call_read_tools, CommerceUnavailable

ROOT = Path(__file__).resolve().parents[1]


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs["action"]
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")


def login(username: str, password: str) -> str:
    """Exercise the browser authorization flow without enabling password grants."""
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    issuer = "http://localhost:8080/realms/resolveai/protocol/openid-connect"
    redirect = "http://localhost:3000/"
    with httpx.Client(trust_env=False, timeout=30, follow_redirects=False) as client:
        response = client.get(issuer + "/auth", params={"client_id":"resolveai-web", "response_type":"code", "redirect_uri":redirect, "scope":"openid", "state":state, "code_challenge":challenge, "code_challenge_method":"S256"})
        response.raise_for_status()
        # Browsers treat localhost as a secure context. httpx intentionally does
        # not, so allow these localhost-only Keycloak session cookies in this
        # protocol test without changing the server's production cookie policy.
        for cookie in client.cookies.jar:
            cookie.secure = False
        form = LoginForm()
        form.feed(response.text)
        assert form.action, "Keycloak login form unavailable"
        response = client.post(form.action, data={**form.fields, "username":username, "password":password})
        assert response.status_code == 302, "Keycloak login did not complete"
        params = parse_qs(urlparse(response.headers["location"]).query)
        assert params.get("state") == [state] and params.get("code"), "OIDC state/code invalid"
        result = client.post(issuer + "/token", data={"grant_type":"authorization_code", "client_id":"resolveai-web", "redirect_uri":redirect, "code":params["code"][0], "code_verifier":verifier})
        result.raise_for_status()
        return result.json()["access_token"]


def verify():
    accounts = json.loads((ROOT / ".local/demo-accounts.json").read_text())
    tokens = {name:login(name, data["password"]) for name, data in accounts.items()}
    checks = []
    with httpx.Client(base_url="http://localhost:8000", trust_env=False, timeout=30) as client:
        assert client.get("/orders", headers={"x-mock-actor":"cust-01", "x-mock-role":"customer"}).status_code == 401
        checks.append("mock_headers_rejected")
        for name in ("customer-one", "customer-two"):
            headers = {"Authorization":"Bearer " + tokens[name]}
            response = client.get("/orders", headers=headers)
            response.raise_for_status()
            assert response.json()
        checks.append("two_customer_logins")
        response = client.post("/chat", headers={"Authorization":"Bearer " + tokens["customer-one"]}, json={"thread_id":"oidc-mcp-check", "message":"包裹到哪里", "order_id":"demo-order-02", "agent_mode":"collab"})
        response.raise_for_status()
        chat = response.json()
        assert chat["status"] == "answered" and chat["findings"] and chat["findings"][0]["tool_calls"] == 2
        checks.append("agent_uses_authenticated_mcp")
        assert client.get("/orders/demo-order-02", headers={"Authorization":"Bearer " + tokens["customer-two"]}).status_code == 404
        checks.append("cross_customer_api_denied")
        for name, path in (("supervisor-demo", "/supervisor/proposals"), ("support-demo", "/tickets")):
            client.get(path, headers={"Authorization":"Bearer " + tokens[name]}).raise_for_status()
        assert client.get("/supervisor/proposals", headers={"Authorization":"Bearer " + tokens["customer-one"]}).status_code == 403
        checks.append("role_isolation")
    results = anyio.run(partial(call_read_tools, tokens["customer-one"], [("get_order", {"order_id":"demo-order-02"}), ("track_shipment", {"order_id":"demo-order-02"})], url="http://localhost:8001/mcp"))
    assert results[0]["id"] == "demo-order-02" and results[1]
    checks.append("authenticated_mcp_reads")
    for name in ("customer-two", "warehouse-demo"):
        try:
            anyio.run(partial(call_read_tools, tokens[name], [("get_order", {"order_id":"demo-order-02"})], url="http://localhost:8001/mcp"))
        except CommerceUnavailable:
            continue
        raise AssertionError("MCP authorization failed")
    checks.append("mcp_customer_and_role_denials")
    print(json.dumps({"status":"ok", "checks":checks}))


if __name__ == "__main__":
    verify()
