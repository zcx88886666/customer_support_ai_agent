"""Track idempotent seven-day refund deadline alerts.

Revision ID: c6230c5ad322
Revises: bf189284837e
"""

from alembic import op
import sqlalchemy as sa


revision = "c6230c5ad322"
down_revision = "bf189284837e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refund_deadline_alerts",
        sa.Column("return_id", sa.String(length=64), sa.ForeignKey("return_requests.id"), primary_key=True),
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("refund_deadline_alerts")
