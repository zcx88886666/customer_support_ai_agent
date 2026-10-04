"""Erase legacy revoked preference values while retaining audit rows.

Revision ID: 7c461acdb21e
Revises: 8a512e96af34
"""

from __future__ import annotations

from alembic import op


revision = "7c461acdb21e"
down_revision = "8a512e96af34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE memory_entries SET value = '' WHERE revoked = TRUE")


def downgrade() -> None:
    # Erased customer preference text cannot be reconstructed.
    pass
