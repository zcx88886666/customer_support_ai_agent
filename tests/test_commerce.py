from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anyio
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from resolveai import agent, commerce_client
from resolveai.schemas import ChatInput
from services.commerce_mcp import server


def test_mcp_requires_its_own_audience_and_customer_identity():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = server.KeycloakVerifier()
    verifier.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()))
    now = datetime.now(timezone.utc)
    claims = {"sub":"user", "iss":server.settings.oidc_issuer, "aud":server.settings.mcp_audience, "iat":now, "exp":now+timedelta(minutes=5), "realm_access":{"roles":["customer"]}, "customer_id":"cust-01"}
    token = jwt.encode(claims, private, algorithm="RS256")
    valid = anyio.run(verifier.verify_token, token)
    assert valid.claims["customer_id"] == "cust-01"
    for change in ({"aud":"resolveai-api"}, {"realm_access":{"roles":["supervisor"]}}, {"customer_id":None}, {"exp":now-timedelta(seconds=1)}):
        assert anyio.run(verifier.verify_token, jwt.encode({**claims, **change}, private, algorithm="RS256")) is None


def test_mcp_client_rejects_write_tools_before_network():
    with pytest.raises(commerce_client.CommerceUnavailable):
        anyio.run(commerce_client.call_read_tools, "synthetic", [("issue_refund", {})])


def test_oidc_agent_uses_mcp_and_never_checkpoints_token(db, monkeypatch):
    monkeypatch.setattr(agent, "settings", replace(agent.settings, auth_mode="oidc"))
    calls = []

    def read(token, order_id):
        calls.append((token, order_id))
        return {"id":order_id, "status":"paid", "version":1}, [{"id":"demo-shipment-02", "status":"in_transit", "delivered_at":None}]

    monkeypatch.setattr(agent, "read_order", read)
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="mcp", message="包裹到哪里", order_id="demo-order-02"), access_token="synthetic-secret")
    assert calls == [("synthetic-secret", "demo-order-02")]
    assert result["findings"][0]["tool_calls"] == 2
    assert "synthetic-secret" not in str(result)
    assert "synthetic-secret" not in str(db.get(agent.m.ThreadState, "mcp").state)

    def fail(*args):
        raise commerce_client.CommerceUnavailable("unavailable")

    monkeypatch.setattr(agent, "read_order", fail)
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="mcp-fail", message="包裹到哪里", order_id="demo-order-02"), access_token="synthetic-secret")
    assert result["findings"][0]["status"] == "error"
    assert "部分证据未核实" in result["answer"]


def test_oidc_mcp_package_choice_stays_inside_owned_order(db, monkeypatch):
    from resolveai import models as m

    db.add(m.Shipment(id="mcp-package-02", order_id="demo-order-02", status="delivered", delivered_at=datetime(2026, 9, 28, 12, tzinfo=timezone.utc), version=1))
    db.flush()
    monkeypatch.setattr(agent, "settings", replace(agent.settings, auth_mode="oidc"))

    def read(_token, order_id):
        return {"id": order_id, "status": "paid", "version": 1}, [
            {"id": "demo-shipment-02", "status": "in_transit", "delivered_at": None},
            {"id": "mcp-package-02", "status": "delivered", "delivered_at": datetime(2026, 9, 28, 12, tzinfo=timezone.utc).isoformat()},
        ]

    monkeypatch.setattr(agent, "read_order", read)
    first = agent.run_chat(db, "cust-01", ChatInput(thread_id="mcp-package", message="查包裹物流", order_id="demo-order-02"), access_token="synthetic-secret")
    assert first["status"] == "clarify" and len(first["shipment_options"]) == 2
    second = agent.run_chat(db, "cust-01", ChatInput(thread_id="mcp-package", message="这个", shipment_id="mcp-package-02"), access_token="synthetic-secret")
    assert second["status"] == "answered"
    assert second["findings"][0]["source_ids"] == ["demo-order-02", "mcp-package-02"]
    assert "synthetic-secret" not in str(db.get(m.ThreadState, "mcp-package").state)
