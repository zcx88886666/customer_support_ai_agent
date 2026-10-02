"""Refresh PostgreSQL search data for published local policy bundles."""

from sqlalchemy import select

from resolveai import models as m
from resolveai.db import SessionLocal
from resolveai.policy_retrieval import index_bundle


def main():
    with SessionLocal.begin() as db:
        bundles = db.scalars(select(m.PolicyBundle.id).where(m.PolicyBundle.status.in_(("active", "verified")))).all()
        counts = {bundle_id: index_bundle(db, bundle_id) for bundle_id in bundles}
    print({"indexed_bundles": counts})


if __name__ == "__main__":
    main()
