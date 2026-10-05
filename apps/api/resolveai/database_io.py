"""Request-aware adapters for pool admission, libpq connection and socket waits.

Only connection establishment runs in bounded daemon threads. Business queries
stay on their caller's thread. Late connections are closed, never pooled.
"""

from concurrent.futures import Future, wait
from contextvars import copy_context
import math
import threading

from psycopg import Connection, errors
from sqlalchemy.pool import QueuePool
from sqlalchemy.util import queue

from .request_budget import BudgetExceeded, current_budget, remaining_io_seconds


class _BudgetQueue(queue.Queue):
    def get(self, block=True, timeout=None):
        active = current_budget() is not None
        if active:
            allowed = remaining_io_seconds(timeout if timeout is not None else 86400)
            if block:
                timeout = allowed
        try:
            return super().get(block, timeout)
        except queue.Empty:
            if active:
                # Preserve ordinary pool timeouts when they precede the budget.
                remaining_io_seconds(86400)
            raise


class BudgetQueuePool(QueuePool):
    # SQLAlchemy2.x internal adapter: never mutate shared Pool._timeout.
    _queue_class = _BudgetQueue


class BudgetConnection(Connection):
    def wait(self, gen, interval=0.1, timeout=None):
        if current_budget() is None:
            return Connection.wait(self, gen, interval=interval, timeout=timeout)
        try:
            allowed = remaining_io_seconds(timeout if timeout is not None else 86400)
            return Connection.wait(self, gen, interval=interval, timeout=allowed)
        except (BudgetExceeded, errors._WaitTimeout):
            # A query/commit may have reached the server. Close locally without
            # an unbounded cancel/rollback handshake; fresh replay is idempotent.
            self.close()
            # SQLAlchemy recognizes this DBAPI error and invalidates closed
            # pooled connections before its caller rolls back.
            raise errors.QueryCanceled("database_io_deadline_expired") from None


_connector_slots = threading.BoundedSemaphore(4)


def _close_late(future):
    if not future.cancelled() and future.exception() is None:
        future.result().close()


def connect_with_deadline(*args, **kwargs):
    kwargs = dict(kwargs)
    kwargs.setdefault("connect_timeout", 5)
    if current_budget() is None:
        return BudgetConnection.connect(*args, **kwargs)
    remaining = remaining_io_seconds(86400)
    # libpq rounds short timeouts up; the caller's float deadline below also
    # bounds DNS and multiple-address attempts. The thread can only connect.
    configured = int(kwargs["connect_timeout"])
    kwargs["connect_timeout"] = max(1, min(configured if configured > 0 else 5, math.ceil(remaining)))
    slots = _connector_slots
    if not slots.acquire(timeout=remaining):
        raise BudgetExceeded("database_connection_admission_expired")
    result = Future()
    context = copy_context()
    def establish():
        try:
            result.set_result(BudgetConnection.connect(*args, **kwargs))
        except BaseException as error:
            result.set_exception(error)
        finally:
            slots.release()
    try:
        thread = threading.Thread(target=context.run, args=(establish,), daemon=True,
                                  name="resolveai-db-connect")
        thread.start()
    except BaseException:
        slots.release()
        raise
    try:
        done, _ = wait([result], timeout=remaining_io_seconds(86400))
        if not done:
            raise BudgetExceeded("database_connection_deadline_expired")
        connection = result.result()
        remaining_io_seconds(86400)
        return connection
    except BaseException:
        result.add_done_callback(_close_late)
        raise


class BudgetLock:
    """PostgresSaver's serialization lock with a caller-local deadline."""
    def __init__(self):
        self.lock = threading.Lock()

    def __enter__(self):
        if current_budget() is None:
            self.lock.acquire()
        elif not self.lock.acquire(timeout=remaining_io_seconds(86400)):
            raise BudgetExceeded("checkpoint_lock_deadline_expired")
        return self

    def __exit__(self, *exc):
        self.lock.release()
