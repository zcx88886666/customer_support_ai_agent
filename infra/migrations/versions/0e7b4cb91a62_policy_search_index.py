"""Version-scoped full-text and local gram-vector policy search.

Revision ID: 0e7b4cb91a62
Revises: 9d24757b98e1
"""

from alembic import op


revision = "0e7b4cb91a62"
down_revision = "9d24757b98e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE TABLE policy_clause_search (
            clause_id varchar(64) PRIMARY KEY REFERENCES policy_clauses(id) ON DELETE CASCADE,
            bundle_id varchar(64) NOT NULL REFERENCES policy_bundles(id),
            content_hash varchar(64) NOT NULL,
            index_version varchar(32) NOT NULL,
            search_terms tsvector NOT NULL,
            gram_vector vector(128) NOT NULL
        )""")
        op.execute("CREATE INDEX ix_policy_search_bundle ON policy_clause_search (bundle_id, content_hash, index_version)")
        op.execute("CREATE INDEX ix_policy_search_terms ON policy_clause_search USING GIN (search_terms)")
        op.execute("CREATE INDEX ix_policy_search_vector ON policy_clause_search USING hnsw (gram_vector vector_cosine_ops)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE policy_clause_search")
