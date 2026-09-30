"""Enable pgvector in PostgreSQL databases.

Revision ID: 9d24757b98e1
Revises: c6230c5ad322
"""

from alembic import op


revision = "9d24757b98e1"
down_revision = "c6230c5ad322"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # The extension may be shared with other tables or have predated this app.
    pass
