"""Inject post-commit checkpoint failures in an isolated PostgreSQL API flow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    admin_url = os.environ.get("APPROVAL_RECOVERY_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("APPROVAL_RECOVERY_PG_ADMIN_URL must connect to a separate PostgreSQL /postgres database")
    name = "ra_approval_recovery_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db_url = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + name, "", ""))
    env = os.environ.copy()
    env.update({"DATABASE_URL": db_url, "LONG_TERM_MEMORY_MODE": "off", "AUTH_MODE": "mock", "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": ""})
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    os.environ.update(env)

    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from resolveai import models as m
    from resolveai.api import app
    from resolveai.approval_checkpoint import _config, build_approval_graph
    from resolveai.checkpoint import parent_checkpointer
    from resolveai.db import SessionLocal, engine
    from resolveai.seed import seed_demo
    from resolveai.worker import issue_approved_once

    with SessionLocal.begin() as db:
        seed_demo(db, datetime.now(timezone.utc))
    customer = {"X-Mock-Actor": "cust-01", "X-Mock-Role": "customer"}
    warehouse = {"X-Mock-Actor": "warehouse-1", "X-Mock-Role": "warehouse"}
    supervisor = {"X-Mock-Actor": "supervisor-1", "X-Mock-Role": "supervisor"}
    checks = []
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/returns", headers=customer, json={"order_id": "demo-order-01", "order_item_id": "demo-item-01", "quantity": 1, "reason": "synthetic recovery check", "confirmed": True, "idempotency_key": "approval-recovery-" + name})
            assert response.status_code == 200, response.text[:200]
            return_id = response.json()["id"]
            assert client.post(f"/warehouse/returns/{return_id}/receipt", headers=warehouse, json={"quantity": 1}).status_code == 200
            assert client.post(f"/warehouse/returns/{return_id}/inspection", headers=warehouse, json={"passed": True, "note": "intact"}).status_code == 200

            with patch("resolveai.approval_checkpoint.start_approval_wait", side_effect=RuntimeError("synthetic checkpoint write failure")):
                assert client.post(f"/returns/{return_id}/proposal", headers=warehouse).status_code == 500
            with SessionLocal() as db:
                proposal = db.scalar(select(m.RefundProposal).where(m.RefundProposal.return_id == return_id))
                assert proposal is not None and proposal.status == "pending"
                assert db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal.id)) is None
                assert db.scalar(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal.id)) is None
                proposal_id = proposal.id
            checks.append("proposal_committed_before_checkpoint_failure_without_refund")

            response = client.post(f"/returns/{return_id}/proposal", headers=warehouse)
            assert response.status_code == 200 and response.json()["id"] == proposal_id
            with SessionLocal() as db:
                with parent_checkpointer() as saver:
                    snapshot = build_approval_graph(db, saver).get_state(_config(proposal_id))
                assert snapshot.next == ("wait_for_supervisor",)
            checks.append("proposal_retry_recreated_pending_checkpoint")

            with patch("resolveai.approval_checkpoint.resume_approval_wait", side_effect=RuntimeError("synthetic resume failure")):
                assert client.post(f"/supervisor/proposals/{proposal_id}/decision", headers=supervisor, json={"approve": True}).status_code == 500
            with SessionLocal() as db:
                approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal_id))
                assert approval is not None and approval.decision == "approved"
                assert db.scalar(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal_id)) is None
            checks.append("supervisor_decision_committed_before_resume_failure_without_refund")

            response = client.post(f"/supervisor/proposals/{proposal_id}/decision", headers=supervisor, json={"approve": True})
            assert response.status_code == 200 and response.json()["id"] == approval.id
            with SessionLocal() as db:
                with parent_checkpointer() as saver:
                    snapshot = build_approval_graph(db, saver).get_state(_config(proposal_id))
                assert snapshot.values.get("status") == "approved" and snapshot.next == ()
            checks.append("decision_retry_resumed_existing_checkpoint")

            assert len(issue_approved_once()) == 1
            assert issue_approved_once() == []
            with SessionLocal() as db:
                ledgers = db.scalars(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal_id)).all()
                assert len(ledgers) == 1 and ledgers[0].amount_cents == db.get(m.OrderItem, "demo-item-01").paid_cents
            checks.append("worker_issued_exactly_once_after_recovery")
    finally:
        engine.dispose()
    print(json.dumps({"database": name, "checks": checks, "passed": len(checks)}))


if __name__ == "__main__":
    main()
