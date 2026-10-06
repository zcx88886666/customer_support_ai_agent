"""Give tickets a stable creation time for newest-first paging.

Revision ID: e6f0c4218b7a
Revises: d4e8f1c2a390
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e6f0c4218b7a"
down_revision = "d4e8f1c2a390"
branch_labels = None
depends_on = None


def upgrade() -> None:
    column = sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("tickets", recreate="always") as batch:
            batch.add_column(column)
    else:
        op.add_column("tickets", column)


def downgrade() -> None:
    linked = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM tickets WHERE return_id IS NOT NULL"))
    if linked:
        raise RuntimeError("Cannot downgrade while linked exception tickets exist; preserve their return links")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("tickets", recreate="always") as batch:
            batch.drop_column("created_at")
    else:
        op.drop_column("tickets", "created_at")
