"""Replay the controlled synthetic refund path through role-checked HTTP APIs."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from resolveai.api import app
from resolveai.db import SessionLocal
from resolveai.seed import seed_demo
from resolveai.worker import issue_approved_once


def call(client, method, path, role, actor, body=None):
    response = client.request(method, path, headers={"x-mock-role": role, "x-mock-actor": actor}, json=body)
    if response.status_code >= 400:
        raise RuntimeError(f"{path}: {response.status_code} {response.text}")
    return response.json()


def main():
    with SessionLocal.begin() as db:
        seed_demo(db, datetime.now(timezone.utc))
    with TestClient(app) as client:
        request = call(client, "POST", "/returns", "customer", "cust-01", {"order_id": "demo-order-01", "order_item_id": "demo-item-01", "quantity": 1, "reason": "synthetic change of mind", "confirmed": True, "idempotency_key": "demo-workflow"})
        return_id = request["id"]
        call(client, "POST", f"/warehouse/returns/{return_id}/receipt", "warehouse", "warehouse-1", {"quantity": 1})
        call(client, "POST", f"/warehouse/returns/{return_id}/inspection", "warehouse", "warehouse-1", {"passed": True, "note": "intact"})
        proposal = call(client, "POST", f"/returns/{return_id}/proposal", "warehouse", "warehouse-1", {})
        call(client, "POST", f"/supervisor/proposals/{proposal['id']}/decision", "supervisor", "supervisor-1", {"approve": True})
        issued = issue_approved_once()
        customer_status = call(client, "GET", f"/returns/{return_id}", "customer", "cust-01")
        audit = call(client, "GET", f"/audit/proposal/{proposal['id']}", "supervisor", "supervisor-1")
    print(json.dumps({"return_id": return_id, "proposal_id": proposal["id"], "proposal_amount_cents": proposal["amount_cents"], "issued_ledger_ids": issued, "customer_status": customer_status["status"], "proposal_audit_actions": [entry["action"] for entry in audit]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
