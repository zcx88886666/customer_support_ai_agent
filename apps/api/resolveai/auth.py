from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from .config import settings


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    customer_id: str | None = None

    def require(self, role: str):
        if role not in self.roles:
            raise HTTPException(403, "Role required")


_jwks = PyJWKClient(settings.oidc_jwks_url)


def principal(authorization: str | None = Header(default=None), x_mock_actor: str | None = Header(default=None), x_mock_role: str | None = Header(default=None)) -> Principal:
    if settings.auth_mode == "mock":
        if not x_mock_actor or x_mock_role not in {"customer", "support", "warehouse", "supervisor"}:
            raise HTTPException(401, "Mock actor and role required")
        return Principal(x_mock_actor, frozenset({x_mock_role}), x_mock_actor if x_mock_role == "customer" else None)
    if settings.auth_mode != "oidc":
        raise HTTPException(503, "Authentication mode invalid")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token = authorization.removeprefix("Bearer ")
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.oidc_audience, issuer=settings.oidc_issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid token") from exc
    roles = frozenset(claims.get("realm_access", {}).get("roles", []))
    customer_id = claims.get("customer_id") if "customer" in roles else None
    if "customer" in roles and not customer_id:
        raise HTTPException(403, "Customer mapping missing")
    return Principal(claims["sub"], roles, customer_id)


PrincipalDep = Depends(principal)
