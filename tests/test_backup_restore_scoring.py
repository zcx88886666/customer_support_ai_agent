"""Equal counts alone cannot prove that restored business facts survived."""

from copy import deepcopy
import hashlib
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from scripts import verify_postgres_backup_restore as backup_restore
from scripts.verify_postgres_backup_restore import restore_checks, snapshot_database, canonical_json_row


def snapshot():
    return {"tables": {"refund_ledger": {"count": 1, "sha256": "original-ledger"},
                       "checkpoints": {"count": 3, "sha256": "original-checkpoints"}},
            "columns": ["paid_cents:int:not_null"], "constraints": ["one-ledger-per-proposal"],
            "indexes": ["ledger-unique"], "extensions": ["vector:0.8.6"], "sequences": [["probe", 42]],
            "sequence_calls": [["probe", 42, True]]}


def test_equal_restored_snapshot_passes():
    assert all(restore_checks(snapshot(), deepcopy(snapshot())).values())


def test_same_ledger_count_with_changed_money_fails():
    before = snapshot()
    after = deepcopy(before)
    after["tables"]["refund_ledger"]["sha256"] = "different-money"
    checks = restore_checks(before, after)
    assert checks["table_counts_match"]
    assert not checks["table_rows_match"]


@pytest.mark.parametrize("field", ["columns", "constraints", "indexes", "extensions", "sequences"])
def test_schema_and_sequence_changes_are_detected(field):
    after = snapshot()
    after[field] = []
    assert not all(restore_checks(snapshot(), after).values())


@pytest.mark.parametrize("change", ["missing", "extra", "empty"])
def test_checkpoint_or_extra_table_difference_fails(change):
    after = snapshot()
    if change == "missing":
        del after["tables"]["checkpoints"]
    elif change == "extra":
        after["tables"]["unrecognized_table"] = {"count": 1, "sha256": "unexpected"}
    else:
        after["tables"]["checkpoints"] = {"count": 0, "sha256": "empty"}
    assert not all(restore_checks(snapshot(), after).values())


def test_equal_sequence_last_value_with_different_next_value_fails():
    after = snapshot()
    after["sequence_calls"][0][2] = False
    assert not all(restore_checks(snapshot(), after).values())


def test_distinct_high_precision_json_numbers_have_distinct_hash_inputs():
    left = canonical_json_row('{"amount":1.00000000000000001}')
    right = canonical_json_row('{"amount":1.00000000000000002}')
    assert left != right
    assert left != canonical_json_row('{"amount":"1.00000000000000001"}')


def test_changed_archive_is_rejected_before_restore_action(tmp_path):
    archive = tmp_path / "database.dump"
    archive.write_bytes(b"original synthetic archive")
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.write_bytes(b"damaged synthetic archive")
    actions = []
    with pytest.raises(ValueError, match="Archive hash mismatch"):
        backup_restore.restore_guarded(archive, expected, lambda: actions.append("restore"))
    assert actions == []
    archive.write_bytes(b"original synthetic archive")
    backup_restore.restore_guarded(archive, expected, lambda: actions.append("restore"))
    assert actions == ["restore"]


@pytest.mark.skipif(not os.environ.get("BACKUP_SCHEMA_PG_ADMIN_URL"), reason="requires disposable PostgreSQL schema database")
def test_snapshot_detects_varchar_length_change_with_same_rows():
    admin_url = os.environ["BACKUP_SCHEMA_PG_ADMIN_URL"]
    parts = urlsplit(admin_url)
    assert parts.scheme == "postgresql" and parts.path == "/postgres"
    name = "ra_schema_" + uuid4().hex[:16]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target_url = urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))
    try:
        with psycopg.connect(target_url) as connection:
            connection.execute("CREATE TABLE refund_ledger (currency varchar(3) NOT NULL)")
            connection.execute("INSERT INTO refund_ledger (currency) VALUES ('USD')")
        before = snapshot_database(target_url)
        with psycopg.connect(target_url) as connection:
            connection.execute("ALTER TABLE refund_ledger ALTER COLUMN currency TYPE varchar(4)")
        after = snapshot_database(target_url)
        checks = restore_checks(before, after)
        assert checks["table_counts_match"]
        assert checks["table_rows_match"]
        assert not checks["columns_match"]
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
