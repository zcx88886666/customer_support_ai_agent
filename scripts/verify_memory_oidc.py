"""Check real OIDC memory endpoints on synthetic demo accounts, then restore state."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from verify_oidc import login

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    accounts = json.loads((ROOT / ".local/demo-accounts.json").read_text(encoding="utf-8"))
    tokens = {name: login(name, accounts[name]["password"]) for name in ("customer-one", "customer-two", "warehouse-demo")}
    headers = {name: {"Authorization": "Bearer " + token} for name, token in tokens.items()}
    checks = []
    with httpx.Client(base_url="http://localhost:8000", trust_env=False, timeout=30) as client:
        before = client.get("/profile/preferences", headers=headers["customer-one"])
        before.raise_for_status()
        original = before.json()
        other = client.get("/profile/preferences", headers=headers["customer-two"])
        other.raise_for_status()
        other_original = other.json()
        assert client.get("/profile/preferences", headers=headers["warehouse-demo"]).status_code == 403
        checks.append("role_denial")
        try:
            if not original["consent"]:
                assert client.put("/profile/preferences/communication_style", headers=headers["customer-one"], json={"value": "concise bullet points", "confirmed": True}).status_code == 403
                client.post("/profile/memory-consent", headers=headers["customer-one"], json={"consent": True}).raise_for_status()
            assert client.put("/profile/preferences/communication_style", headers=headers["customer-one"], json={"value": "concise bullet points", "confirmed": False}).status_code == 403
            client.put("/profile/preferences/communication_style", headers=headers["customer-one"], json={"value": "concise bullet points", "confirmed": True}).raise_for_status()
            client.put("/profile/preferences/communication_style", headers=headers["customer-one"], json={"value": "detailed paragraphs", "confirmed": True}).raise_for_status()
            current = client.get("/profile/preferences", headers=headers["customer-one"])
            current.raise_for_status()
            assert current.json()["preferences"]["communication_style"] == "detailed paragraphs"
            assert client.get("/profile/preferences", headers=headers["customer-two"]).json() == other_original
            checks.append("consent_confirmation_correction_and_isolation")
            client.delete("/profile/preferences/communication_style", headers=headers["customer-one"]).raise_for_status()
            after_delete = client.get("/profile/preferences", headers=headers["customer-one"])
            after_delete.raise_for_status()
            assert "communication_style" not in after_delete.json()["preferences"]
            checks.append("delete_not_retrievable")
        finally:
            original_style = original["preferences"].get("communication_style")
            if original_style is not None:
                client.put("/profile/preferences/communication_style", headers=headers["customer-one"], json={"value": original_style, "confirmed": True}).raise_for_status()
            else:
                client.delete("/profile/preferences/communication_style", headers=headers["customer-one"]).raise_for_status()
            if not original["consent"]:
                client.post("/profile/memory-consent", headers=headers["customer-one"], json={"consent": False}).raise_for_status()
            restored = client.get("/profile/preferences", headers=headers["customer-one"])
            restored.raise_for_status()
            assert restored.json() == original
            checks.append("original_profile_restored")
    print(json.dumps({"status": "ok", "checks": checks}))


if __name__ == "__main__":
    main()
