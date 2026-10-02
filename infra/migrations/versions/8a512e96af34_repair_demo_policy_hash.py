"""Repair the fixed demo policy's legacy marker hash to its content hash.

Revision ID: 8a512e96af34
Revises: 0e7b4cb91a62
"""

import hashlib
import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text


revision = "8a512e96af34"
down_revision = "0e7b4cb91a62"
branch_labels = None
depends_on = None

EXPECTED = [
    ("clause-exceptions", "例外商品", "非实物及已明确告知并确认的特殊商品不适用无理由退货。"),
    ("clause-inspection", "仓库质检", "实际收到退货后核对数量与商品状态；异常转人工。"),
    ("clause-refund", "模拟退款", "仓库质检通过并经主管批准后，按实付分摊额模拟原路退款。"),
    ("clause-window", "七日无理由退货申请期", "已签收合格实物商品，自签收次日起七个自然日内可申请退货。"),
]


def upgrade() -> None:
    connection = op.get_bind()
    bundle = connection.execute(text("SELECT content_hash, window_days, effective_from FROM policy_bundles WHERE id='policy-demo-v1'")).mappings().first()
    legacy_hash = hashlib.sha256(b"resolveai-demo-policy-v1").hexdigest()
    if not bundle or bundle["content_hash"] != legacy_hash:
        return
    clauses = connection.execute(text("SELECT id, title, body FROM policy_clauses WHERE bundle_id='policy-demo-v1' ORDER BY id")).all()
    if [tuple(row) for row in clauses] != EXPECTED:
        raise RuntimeError("Legacy demo policy content differs from the expected synthetic fixture")
    effective_from = bundle["effective_from"]
    if isinstance(effective_from, str):
        effective_from = datetime.fromisoformat(effective_from)
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=timezone.utc)
    document = {
        "window_days": bundle["window_days"],
        "effective_from": effective_from.isoformat(),
        "clauses": [{"id": clause_id, "title": title, "body": body} for clause_id, title, body in EXPECTED],
    }
    digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    connection.execute(text("UPDATE policy_bundles SET content_hash=:digest WHERE id='policy-demo-v1'"), {"digest": digest})


def downgrade() -> None:
    # Keep the repaired content hash; restoring the obsolete marker would make
    # a historical rollback unverifiable.
    pass
