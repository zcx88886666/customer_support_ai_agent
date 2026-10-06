"""A reassigned support actor must not read messages added after revocation."""

from datetime import datetime, timezone
import os
from threading import Event, Thread
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from resolveai import models as m
from resolveai.api import get_ticket
from resolveai.auth import Principal
from resolveai.db import Base, make_engine


@pytest.mark.skipif(not os.environ.get("TICKET_CONCURRENCY_PG_ADMIN_URL"), reason="requires isolated PostgreSQL concurrency database")
def test_reassignment_cannot_insert_message_into_old_support_read():
    admin_url = os.environ["TICKET_CONCURRENCY_PG_ADMIN_URL"]
    parts = urlsplit(admin_url)
    assert parts.scheme == "postgresql" and parts.path == "/postgres"
    name = "ra_ticket_race_" + uuid4().hex[:16]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    uri = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + name, "", ""))
    engine = make_engine(uri)
    factory = sessionmaker(engine, expire_on_commit=False)
    writer_done = Event()
    writer_started = Event()
    writer_errors = []
    writer = None
    try:
        Base.metadata.create_all(engine)
        with factory.begin() as db:
            db.add_all([m.Customer(id="cust-01", display_name="One"), m.Customer(id="cust-02", display_name="Two")])
            db.flush()
            db.add(m.Ticket(id="ticket-race", customer_id="cust-01", topic="synthetic dispute", support_actor_id="support-a"))
            db.flush()
            db.add(m.ConversationMessage(id="first", ticket_id="ticket-race", actor_type="customer", body="Before reassignment", created_at=datetime.now(timezone.utc)))

        def reassign_and_message():
            writer_started.set()
            try:
                with factory.begin() as db:
                    ticket = db.get(m.Ticket, "ticket-race", with_for_update=True)
                    ticket.support_actor_id = "support-b"
                    db.add(m.ConversationMessage(id="later", ticket_id=ticket.id, actor_type="customer", body="After reassignment", created_at=datetime.now(timezone.utc)))
            except Exception as exc:
                writer_errors.append(type(exc).__name__)
            finally:
                writer_done.set()

        armed = True

        def after_ticket_read(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal armed, writer
            if armed and "FROM tickets" in statement and "WHERE tickets.id" in statement:
                armed = False
                writer = Thread(target=reassign_and_message)
                writer.start()
                assert writer_started.wait(timeout=2)
                assert not writer_done.wait(timeout=0.3), "Reassignment committed during the detail read"

        event.listen(engine, "after_cursor_execute", after_ticket_read)
        with factory() as db:
            old_view = get_ticket("ticket-race", Principal("support-a", frozenset({"support"})), db)
        assert writer_done.wait(timeout=10)
        writer.join(timeout=1)
        assert not writer_errors
        assert [message["body"] for message in old_view["messages"]] == ["Before reassignment"]
        with factory() as db:
            fresh = get_ticket("ticket-race", Principal("support-b", frozenset({"support"})), db)
        assert [message["body"] for message in fresh["messages"]] == ["Before reassignment", "After reassignment"]
    finally:
        if writer and writer.is_alive():
            writer.join(timeout=10)
        engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
