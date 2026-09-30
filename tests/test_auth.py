from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from resolveai import auth


def test_oidc_signature_audience_expiry_and_customer_mapping(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth, "settings", replace(auth.settings, auth_mode="oidc"))
    monkeypatch.setattr(auth, "_jwks", SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=private.public_key())))
    now = datetime.now(timezone.utc)
    claims = {"sub": "keycloak-subject", "customer_id": "cust-01", "realm_access": {"roles": ["customer"]}, "iss": auth.settings.oidc_issuer, "aud": auth.settings.oidc_audience, "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(claims, private, algorithm="RS256")
    principal = auth.principal(authorization="Bearer " + token)
    assert principal.customer_id == "cust-01"
    assert "customer" in principal.roles
    for changed in ({"aud": "wrong-api"}, {"exp": now - timedelta(seconds=1)}, {"customer_id": None}):
        bad = jwt.encode({**claims, **changed}, private, algorithm="RS256")
        with pytest.raises(HTTPException):
            auth.principal(authorization="Bearer " + bad)
