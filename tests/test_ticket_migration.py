from datetime import timedelta

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from resolveai import domain as d, models as m
from resolveai.seed import DEMO_CLOCK, seed_demo


def test_ticket_link_migration_preserves_existing_and_backfills_exception(monkeypatch, tmp_path):
    url = "sqlite:///" + str(tmp_path / "legacy.db")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "7c461acdb21e")
    engine = create_engine(url)
    try:
        with Session(engine) as db, db.begin():
            seed_demo(db, DEMO_CLOCK)
            request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "not needed", True, "legacy-exception", DEMO_CLOCK)
            receipt = d.record_receipt(db, "warehouse-a", request.id, 1, DEMO_CLOCK + timedelta(hours=1))
            db.add(m.Inspection(receipt_id=receipt.id, passed=False, note="damaged carton", inspected_at=DEMO_CLOCK + timedelta(hours=2)))
            request.status = "exception"
            return_id = request.id
            db.flush()
            db.execute(text("INSERT INTO tickets (id, customer_id, topic, status) VALUES ('legacy-ticket', 'cust-01', 'legacy question', 'open')"))

        command.upgrade(config, "head")
        with Session(engine) as db:
            linked = db.scalar(select(m.Ticket).where(m.Ticket.return_id == return_id))
            assert linked is not None and linked.status == "open"
            assert db.get(m.Ticket, "legacy-ticket").return_id is None
            assert "damaged carton" in db.scalar(select(m.ConversationMessage).where(m.ConversationMessage.ticket_id == linked.id)).body
            assert db.scalar(select(m.AuditEvent).where(m.AuditEvent.entity_id == linked.id)).action == "create_ticket_backfill"
            assert any(fk["constrained_columns"] == ["return_id"] for fk in inspect(engine).get_foreign_keys("tickets"))
            assert any(index["name"] == "ix_tickets_return_id" and index["unique"] for index in inspect(engine).get_indexes("tickets"))
        command.downgrade(config, "e6f0c4218b7a")
        command.upgrade(config, "head")
        with pytest.raises(RuntimeError, match="linked exception tickets"):
            command.downgrade(config, "7c461acdb21e")
        command.upgrade(config, "head")
        with Session(engine) as db:
            assert db.scalar(select(m.Ticket).where(m.Ticket.return_id == return_id)) is not None
    finally:
        engine.dispose()


def test_return_review_migration_preserves_tickets_and_guards_live_review_keys(monkeypatch, tmp_path):
    url = "sqlite:///" + str(tmp_path / "review-legacy.db")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "e6f0c4218b7a")
    engine = create_engine(url)
    try:
        with Session(engine) as db, db.begin():
            seed_demo(db, DEMO_CLOCK)
            db.execute(text("INSERT INTO tickets (id, customer_id, topic, status) VALUES ('old-ticket', 'cust-01', 'older inquiry', 'open')"))
        command.upgrade(config, "head")
        with Session(engine) as db, db.begin():
            assert db.get(m.Ticket, "old-ticket").review_key is None
            ticket, reason = d.request_return_review(db, "cust-01", "demo-order-02", "demo-item-02", 1,
                                                     "not delivered", True, "migration-review-key", DEMO_CLOCK)
            assert reason == "delivery_unverified"
            assert ticket.review_key == "migration-review-key"
        assert any(index["name"] == "uq_tickets_customer_review_key" and index["unique"] for index in inspect(engine).get_indexes("tickets"))
        with pytest.raises(RuntimeError, match="return review tickets exist"):
            command.downgrade(config, "e6f0c4218b7a")
        with Session(engine) as db:
            assert db.get(m.Ticket, "old-ticket") is not None
            assert db.scalar(select(m.Ticket).where(m.Ticket.review_key == "migration-review-key")) is not None
    finally:
        engine.dispose()
