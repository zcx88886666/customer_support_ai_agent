"""Caller-local admission and connection cleanup under a shared deadline."""

from concurrent.futures import ThreadPoolExecutor
import socket
import threading
import time

import psycopg
import pytest
from sqlalchemy.pool import QueuePool

from resolveai import database_io as io
from resolveai.request_budget import BudgetExceeded, BudgetLimits, RequestBudget, budget_scope


def budget(seconds=0.12):
    return RequestBudget(BudgetLimits(timeout_seconds=seconds))


def test_contended_pool_uses_each_callers_remaining_budget():
    pool = io.BudgetQueuePool(lambda: object(), pool_size=1, max_overflow=0, timeout=2, reset_on_return=None)
    held = pool.connect()
    def acquire(seconds):
        started = time.monotonic()
        with budget_scope(budget(seconds)), pytest.raises(BudgetExceeded):
            pool.connect()
        return time.monotonic() - started
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            fast = executor.submit(acquire, 0.08)
            slow = executor.submit(acquire, 0.22)
            assert 0.06 < fast.result(timeout=1) < 0.18
            assert 0.20 < slow.result(timeout=1) < 0.40
        assert pool.timeout() == 2
    finally:
        held.close()
        pool.dispose()


def test_standard_pool_timeout_still_applies_without_budget():
    pool = io.BudgetQueuePool(lambda: object(), pool_size=1, max_overflow=0, timeout=0.03, reset_on_return=None)
    held = pool.connect()
    from sqlalchemy.exc import TimeoutError
    try:
        with pytest.raises(TimeoutError):
            pool.connect()
    finally:
        held.close()
        pool.dispose()


def test_expired_connector_closes_late_result_and_releases_slot(monkeypatch):
    finish = threading.Event()
    closed = threading.Event()
    connector_started = threading.Event()
    class Connection:
        def close(self):
            closed.set()
    def connect(*args, **kwargs):
        connector_started.set()
        assert finish.wait(timeout=2)
        return Connection()
    monkeypatch.setattr(io.BudgetConnection, 'connect', connect)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(io, '_connector_slots', slots)
    try:
        started = time.monotonic()
        with budget_scope(budget()), pytest.raises(BudgetExceeded):
            io.connect_with_deadline('synthetic')
        assert connector_started.is_set() and time.monotonic() - started < 0.3
        assert not slots.acquire(blocking=False)
    finally:
        finish.set()
    assert closed.wait(timeout=1)
    assert slots.acquire(timeout=1)
    slots.release()


def test_connector_saturation_rejects_without_starting_more_connections(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(io, '_connector_slots', slots)
    called = []
    monkeypatch.setattr(io.BudgetConnection, 'connect', lambda *a, **k: called.append(True))
    try:
        with budget_scope(budget(0.05)), pytest.raises(BudgetExceeded):
            io.connect_with_deadline('synthetic')
        assert called == []
    finally:
        slots.release()


def test_connector_copies_budget_and_preserves_unexpected_errors(monkeypatch):
    from resolveai.request_budget import current_budget
    original = budget()
    seen = []
    def connect(*args, **kwargs):
        seen.append(current_budget())
        raise psycopg.errors.InvalidCatalogName('synthetic missing database')
    monkeypatch.setattr(io.BudgetConnection, 'connect', connect)
    with budget_scope(original), pytest.raises(psycopg.errors.InvalidCatalogName):
        io.connect_with_deadline('synthetic')
    assert seen == [original]


def test_socket_wait_closes_uncertain_connection_at_deadline():
    reader, writer = socket.socketpair()
    class Probe:
        pgconn = type('PG', (), {'socket': reader.fileno()})()
        closed = False
        def close(self):
            self.closed = True
    probe = Probe()
    def stalled():
        while True:
            yield psycopg.waiting.Wait.R
    started = time.monotonic()
    try:
        with budget_scope(budget()), pytest.raises(psycopg.errors.QueryCanceled):
            io.BudgetConnection.wait(probe, stalled())
        assert probe.closed and 0.10 < time.monotonic() - started < 0.3
    finally:
        reader.close()
        writer.close()


def test_checkpoint_lock_wait_is_bounded_and_reusable():
    lock = io.BudgetLock()
    entered = threading.Event()
    release = threading.Event()
    def hold():
        with lock:
            entered.set()
            release.wait(timeout=2)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(timeout=1)
    try:
        with budget_scope(budget(0.05)), pytest.raises(BudgetExceeded):
            with lock:
                pytest.fail('Must not acquire the occupied checkpoint lock')
    finally:
        release.set()
        thread.join(timeout=1)
    with lock:
        pass
