"""Read-only Commerce MCP with Keycloak JWT and object authorization."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import jwt
from jwt import PyJWKClient
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl
from sqlalchemy import select

from resolveai import domain as d, models as m
from resolveai.config import settings
from resolveai.db import SessionLocal


class KeycloakVerifier:
    def __init__(self):
        self.jwks = PyJWKClient(settings.oidc_jwks_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.mcp_audience, issuer=settings.oidc_issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWTError:
            return None
        roles = claims.get("realm_access", {}).get("roles", [])
        if "customer" not in roles or not claims.get("customer_id"):
            return None
        return AccessToken(token=token, client_id=claims.get("azp", "resolveai-web"), scopes=[], expires_at=claims["exp"], resource=settings.mcp_audience, subject=claims["sub"], claims={"customer_id": claims["customer_id"], "roles": roles})


resource_url = os.getenv("MCP_RESOURCE_URL", "http://localhost:8001/mcp")
mcp = MCPServer("ResolveAI Commerce", token_verifier=KeycloakVerifier(), auth=AuthSettings(issuer_url=AnyHttpUrl(settings.oidc_issuer), resource_server_url=AnyHttpUrl(resource_url), required_scopes=[], validate_token_resource=False))


def customer_id() -> str:
    token = get_access_token()
    if not token or not token.claims or "customer" not in token.claims.get("roles", []):
        raise PermissionError("Customer authentication required")
    return token.claims["customer_id"]


@mcp.tool(description="Read one authenticated customer's verified order; no write access")
def get_order(order_id: str) -> dict:
    with SessionLocal() as db:
        order = d.owned_order(db, customer_id(), order_id)
        return {"id": order.id, "status": order.status, "version": order.version, "items": [{"id": item.id, "quantity": item.quantity} for item in order.items]}


@mcp.tool(description="Read shipments belonging to one authenticated customer's order")
def track_shipment(order_id: str) -> list[dict]:
    with SessionLocal() as db:
        d.owned_order(db, customer_id(), order_id)
        return [{"id": shipment.id, "status": shipment.status, "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None, "version": shipment.version} for shipment in db.scalars(select(m.Shipment).where(m.Shipment.order_id == order_id)).all()]


@mcp.tool(description="Read deterministic return eligibility; never creates a return or refund")
def check_return_eligibility(order_id: str, order_item_id: str, quantity: int = 1) -> dict:
    with SessionLocal() as db:
        return d.eligibility(db, customer_id(), order_id, order_item_id, quantity, datetime.now(timezone.utc))


app = mcp.streamable_http_app(host=os.getenv("MCP_HOST", "127.0.0.1"))
