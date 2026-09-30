from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./resolveai-dev.db")
    auth_mode: str = os.getenv("AUTH_MODE", "mock")
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "http://localhost:8080/realms/resolveai")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "resolveai-api")
    oidc_jwks_url: str = os.getenv("OIDC_JWKS_URL", "http://localhost:8080/realms/resolveai/protocol/openid-connect/certs")
    prompt_release: str = os.getenv("PROMPT_RELEASE", "release-v1")


settings = Settings()
