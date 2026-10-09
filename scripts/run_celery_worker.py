"""Start one private Celery worker on its designated job queue."""

from __future__ import annotations

import argparse

from celery import Celery

from resolveai.jobs import app


def queue_for_kind(celery_app: Celery, kind: str) -> str:
    if kind == "operational":
        queue = celery_app.conf.task_default_queue
    elif kind == "bulk":
        queue = celery_app.conf.task_default_queue.removesuffix("jobs") + "bulk"
    else:
        raise ValueError("Invalid Celery worker kind")
    if queue not in celery_app.amqp.queues:
        raise ValueError("Configured Celery worker queue is missing")
    return queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("operational", "bulk"), required=True)
    args = parser.parse_args()
    queue = queue_for_kind(app, args.kind)
    concurrency = 2 if args.kind == "operational" else 1
    app.worker_main(["worker", "--loglevel=INFO", f"--concurrency={concurrency}",
                     f"--queues={queue}", f"--hostname={args.kind}@%h"])


if __name__ == "__main__":
    main()
