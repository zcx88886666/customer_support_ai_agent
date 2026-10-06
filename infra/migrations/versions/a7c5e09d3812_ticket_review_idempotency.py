"""Store per-customer idempotency for explicit return eligibility review tickets.

Revision ID: a7c5e09d3812
Revises: e6f0c4218b7a
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "a7c5e09d3812"
down_revision = "e6f0c4218b7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("review_key", sa.String(100), nullable=True))
    op.add_column("tickets", sa.Column("review_payload_hash", sa.String(64), nullable=True))
    op.create_index("uq_tickets_customer_review_key", "tickets", ["customer_id", "review_key"], unique=True)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM tickets WHERE review_key IS NOT NULL")):
        raise RuntimeError("Cannot downgrade while return review tickets exist")
    op.drop_index("uq_tickets_customer_review_key", table_name="tickets")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("tickets", recreate="always") as batch:
            batch.drop_column("review_payload_hash")
            batch.drop_column("review_key")
    else:
        op.drop_column("tickets", "review_payload_hash")
        op.drop_column("tickets", "review_key")
