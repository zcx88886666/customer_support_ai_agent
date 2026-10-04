"""Check parent-graph language preference through real OIDC, then restore it."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx

from verify_oidc import login


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    accounts = json.loads((ROOT / ".local/demo-accounts.json").read_text(encoding="utf-8"))
    owner_token = login("customer-one", accounts["customer-one"]["password"])
    other_token = login("customer-two", accounts["customer-two"]["password"])
    owner = {"Authorization": "Bearer " + owner_token}
    other = {"Authorization": "Bearer " + other_token}
    checks = []
    with httpx.Client(base_url="http://localhost:8000", trust_env=False, timeout=60) as client:
        before = client.get("/profile/preferences", headers=owner)
        before.raise_for_status()
        original = before.json()
        other_before = client.get("/profile/preferences", headers=other)
        other_before.raise_for_status()
        try:
            if not original["consent"]:
                client.post("/profile/memory-consent", headers=owner, json={"consent": True}).raise_for_status()
            client.put("/profile/preferences/language", headers=owner, json={"value": "English", "confirmed": True}).raise_for_status()
            body = {"thread_id": "language-oidc-" + uuid4().hex[:12], "message": "包裹没到能退吗", "order_id": "demo-order-02", "agent_mode": "collab"}
            response = client.post("/chat", headers=owner, json=body)
            response.raise_for_status()
            result = response.json()
            assert result["status"] == "answered" and "Delivery has not been confirmed" in result["answer"]
            assert {finding["source_version"] for finding in result["findings"]} == {"1", "policy-demo-v1"}
            checks.append("english_parent_answer_with_verified_order_and_policy")

            body["thread_id"] = "language-oidc-other-" + uuid4().hex[:12]
            body["order_id"] = "demo-order-05"
            response = client.post("/chat", headers=other, json=body)
            response.raise_for_status()
            assert "查到的订单事实" in response.json()["answer"]
            assert client.get("/profile/preferences", headers=other).json() == other_before.json()
            checks.append("other_customer_language_unchanged")

            client.delete("/profile/preferences/language", headers=owner).raise_for_status()
            body["thread_id"] = "language-oidc-revoked-" + uuid4().hex[:12]
            body["order_id"] = "demo-order-02"
            response = client.post("/chat", headers=owner, json=body)
            response.raise_for_status()
            assert "尚未确认签收" in response.json()["answer"]
            checks.append("revoked_language_not_used")
        finally:
            previous = original["preferences"].get("language")
            if previous is not None:
                client.put("/profile/preferences/language", headers=owner, json={"value": previous, "confirmed": True}).raise_for_status()
            else:
                client.delete("/profile/preferences/language", headers=owner).raise_for_status()
            if not original["consent"]:
                client.post("/profile/memory-consent", headers=owner, json={"consent": False}).raise_for_status()
            restored = client.get("/profile/preferences", headers=owner)
            restored.raise_for_status()
            assert restored.json() == original
            checks.append("original_profile_restored")
    print(json.dumps({"status": "ok", "checks": checks}))


if __name__ == "__main__":
    main()
