"""Private JSON jobs bound to one database and namespace, never model authority."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re

from celery import Celery
from kombu import Exchange, Queue
from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import sessionmaker

from . import bulk_jobs, evaluation_jobs, models as m, worker
from .config import settings
from .db import make_engine
from .policy_retrieval import index_bundle
from .prompts import ROOT


def make_app(namespace: str, database_url: str, broker_url: str,
             *, refund_batch_size: int = 100,
             bulk_data_root: Path | None = None) -> Celery:
    if not isinstance(namespace, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", namespace):
        raise ValueError("Invalid job namespace")
    if type(refund_batch_size) is not int or not 1 <= refund_batch_size <= 1000:
        raise ValueError("Refund batch size must be an integer from 1 to 1000")
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql" and (
        not url.host or not url.port or not url.database
        or set(url.query) & {"service", "host", "hostaddr", "port", "dbname", "database"}
    ):
        # libpq defaults (including PGHOST/PGPORT/PGDATABASE and service files)
        # can select different fixtures despite identical parsed URLs.
        raise ValueError("Jobs require an explicit PostgreSQL host/port/database target")
    database = str(Path(url.database).resolve()) if url.get_backend_name() == "sqlite" and url.database else url.database
    identity = (url.get_backend_name(), url.host, url.port or (5432 if url.get_backend_name() == "postgresql" else None),
                database, sorted(url.query.items()))
    digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    prefix = f"resolveai:{namespace}:{digest}:"
    celery_namespace = namespace
    celery_app = Celery("resolveai", broker=broker_url, set_as_current=False)
    bulk_queue = prefix + "bulk"
    celery_app.conf.update(
        accept_content=["json"], task_serializer="json", result_serializer="json",
        task_ignore_result=True, result_backend=None, task_store_errors_even_if_ignored=False,
        task_default_queue=prefix + "jobs", task_default_exchange=prefix + "jobs",
        task_default_routing_key=prefix + "jobs", task_create_missing_queues=False,
        task_queues=(
            Queue(prefix + "jobs", Exchange(prefix + "jobs", type="direct"), routing_key=prefix + "jobs"),
            Queue(bulk_queue, Exchange(bulk_queue, type="direct"), routing_key=bulk_queue),
        ),
        broker_transport_options={"global_keyprefix": prefix, "visibility_timeout": 90,
                                  "socket_connect_timeout": 3, "socket_timeout": 3},
        broker_connection_timeout=3, broker_connection_max_retries=3,
        broker_connection_retry_on_startup=True,
        task_publish_retry_policy={"max_retries": 3, "interval_start": 0.2, "interval_step": 0.2, "interval_max": 1},
        task_acks_late=True, task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1, worker_concurrency=2,
        task_soft_time_limit=45, task_time_limit=60,
        timezone="UTC", enable_utc=True,
        beat_schedule={
            "refunds": {"task": "resolveai.jobs.refunds", "schedule": 30.0, "args": (namespace,), "options": {"expires": 90}},
            "deadlines": {"task": "resolveai.jobs.deadlines", "schedule": 30.0, "args": (namespace,), "options": {"expires": 90}},
            "policy_index": {"task": "resolveai.jobs.policy_index", "schedule": 300.0, "args": (namespace,), "options": {"expires": 90}},
        },
    )

    @contextmanager
    def database_factory(received_namespace: str):
        if received_namespace != namespace:
            raise ValueError("Job namespace mismatch")
        # Create connections in the executing child, never before prefork.
        engine = make_engine(database_url)
        try:
            yield sessionmaker(engine, expire_on_commit=False)
        finally:
            engine.dispose()

    task_options = {"shared": False, "autoretry_for": (OperationalError, InterfaceError, BrokerOperationalError),
                    "retry_backoff": True, "retry_backoff_max": 8, "retry_kwargs": {"max_retries": 3}}

    @celery_app.task(name="resolveai.jobs.refunds", **task_options)
    def refunds(namespace: str, after_id: str | None = None) -> dict[str, int]:
        with database_factory(namespace) as factory:
            batch = worker.issue_approved_batch(session_factory=factory, batch_size=refund_batch_size,
                                                after_id=after_id)
        if batch.may_have_more:
            refunds.apply_async(args=(namespace, batch.last_candidate_id))
        return {"issued_count": len(batch.issued_ids)}

    @celery_app.task(name="resolveai.jobs.deadlines", **task_options)
    def deadlines(namespace: str) -> dict[str, int]:
        with database_factory(namespace) as factory:
            return {"alert_count": len(worker.alert_refund_deadlines_once(session_factory=factory, batch_size=100))}

    @celery_app.task(name="resolveai.jobs.policy_index", **task_options)
    def policy_index(namespace: str) -> dict[str, int]:
        with database_factory(namespace) as factory:
            with factory.begin() as db:
                ids = db.scalars(select(m.PolicyBundle.id).where(m.PolicyBundle.status.in_(("active", "verified"))).with_for_update()).all()
                return {"bundle_count": len(ids), "clause_count": sum(index_bundle(db, bundle_id) for bundle_id in ids)}

    @celery_app.task(name="resolveai.jobs.generate_world", shared=False,
                     acks_late=True, reject_on_worker_lost=True,
                     queue=bulk_queue,
                     soft_time_limit=900, time_limit=960)
    def generate_world(namespace: str, output_name: str, profile: str,
                       seed: int, clock: str) -> dict[str, str | int]:
        if namespace != celery_namespace:
            raise ValueError("Job namespace mismatch")
        return bulk_jobs.generate_world(bulk_data_root or ROOT / "data/generated",
                                        output_name, profile, seed, clock)

    @celery_app.task(name="resolveai.jobs.development_eval", shared=False,
                     acks_late=True, reject_on_worker_lost=True,
                     queue=bulk_queue, soft_time_limit=900, time_limit=960)
    def development_eval(namespace: str, suite: str) -> dict:
        if namespace != celery_namespace:
            raise ValueError("Job namespace mismatch")
        result = evaluation_jobs.run_development_eval(
            suite, ROOT / "evals/reports/queued", development_eval.request.id)
        if result["status"] != "pass":
            raise RuntimeError(f"Development evaluation {suite} {result['status']}; see its local job report")
        return result

    return celery_app


app = make_app(os.environ.get("JOB_NAMESPACE", "dev"), settings.database_url,
               os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"))
