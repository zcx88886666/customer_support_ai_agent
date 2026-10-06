"""Link inspection exception tickets to the affected return.

Revision ID: d4e8f1c2a390
Revises: 7c461acdb21e
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "d4e8f1c2a390"
down_revision = "7c461acdb21e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("return_id", sa.String(64), nullable=True))
        batch.create_foreign_key("fk_tickets_return_id", "return_requests", ["return_id"], ["id"])
        batch.create_index("ix_tickets_return_id", ["return_id"], unique=True)

    connection = op.get_bind()
    exceptions = connection.execute(sa.text("""
        SELECT r.id AS return_id, r.customer_id, r.order_id, r.policy_bundle_id,
               w.actor_id, i.id AS inspection_id, i.note, i.inspected_at
        FROM return_requests r
        JOIN warehouse_receipts w ON w.return_id = r.id
        JOIN inspections i ON i.receipt_id = w.id
        WHERE r.status = 'exception' AND i.passed = FALSE
          AND NOT EXISTS (SELECT 1 FROM tickets t WHERE t.return_id = r.id)
    """)).mappings().all()
    tickets = sa.table("tickets", sa.column("id"), sa.column("customer_id"), sa.column("order_id"),
                       sa.column("return_id"), sa.column("topic"), sa.column("status"))
    messages = sa.table("conversation_messages", sa.column("id"), sa.column("ticket_id"),
                        sa.column("actor_type"), sa.column("body"), sa.column("created_at", sa.DateTime(timezone=True)))
    events = sa.table("audit_events", sa.column("id"), sa.column("actor_id"), sa.column("action"),
                      sa.column("entity_type"), sa.column("entity_id"), sa.column("policy_bundle_id"),
                      sa.column("details", sa.JSON()), sa.column("created_at", sa.DateTime(timezone=True)))
    for row in exceptions:
        ticket_id = uuid4().hex
        when = row["inspected_at"]
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        summary = "Warehouse inspection did not pass; manual review required."
        if row["note"] and row["note"].strip():
            summary += " Note: " + row["note"].strip()[:200]
        connection.execute(sa.insert(tickets).values(id=ticket_id, customer_id=row["customer_id"],
                           order_id=row["order_id"], return_id=row["return_id"],
                           topic="return inspection exception", status="open"))
        connection.execute(sa.insert(messages).values(id=uuid4().hex, ticket_id=ticket_id,
                           actor_type="warehouse", body=summary, created_at=when))
        connection.execute(sa.insert(events).values(id=uuid4().hex, actor_id="system:migration",
                           action="create_ticket_backfill", entity_type="ticket", entity_id=ticket_id,
                           policy_bundle_id=row["policy_bundle_id"],
                           details={"return_id": row["return_id"], "inspection_id": row["inspection_id"]},
                           created_at=datetime.now(timezone.utc)))


def downgrade() -> None:
    linked = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM tickets WHERE return_id IS NOT NULL"))
    if linked:
        raise RuntimeError("Cannot downgrade while linked exception tickets exist; preserve their return links")
    with op.batch_alter_table("tickets") as batch:
        batch.drop_index("ix_tickets_return_id")
        batch.drop_constraint("fk_tickets_return_id", type_="foreignkey")
        batch.drop_column("return_id")
