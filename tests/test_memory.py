from __future__ import annotations

import pytest

from resolveai.domain import DomainError
from resolveai.memory import delete_preference, list_preferences, set_consent, upsert_preference
from resolveai.models import MemoryEntry


def test_consent_correction_deletion_and_isolation(db):
    with pytest.raises(DomainError):
        upsert_preference(db, "cust-01", "language", "English", True)
    set_consent(db, "cust-01", True)
    upsert_preference(db, "cust-01", "language", "English", True)
    assert list_preferences(db, "cust-01") == {"language": "English"}
    assert list_preferences(db, "cust-02") == {}
    upsert_preference(db, "cust-01", "language", "中文", True)
    assert list_preferences(db, "cust-01") == {"language": "中文"}
    delete_preference(db, "cust-01", "language")
    assert list_preferences(db, "cust-01") == {}
    upsert_preference(db, "cust-01", "language", "English", True)
    set_consent(db, "cust-01", False)
    assert list_preferences(db, "cust-01") == {}


def test_memory_rejects_sensitive_content(db):
    set_consent(db, "cust-01", True)
    with pytest.raises(DomainError):
        upsert_preference(db, "cust-01", "communication_style", "my address is 12345678901", True)


def test_memory_rejects_invalid_legacy_entry_on_read(db):
    set_consent(db, "cust-01", True)
    db.add(MemoryEntry(customer_id="cust-01", key="address", value="do not reveal", revoked=False))
    db.flush()
    with pytest.raises(DomainError) as error:
        list_preferences(db, "cust-01")
    assert error.value.status == 503
