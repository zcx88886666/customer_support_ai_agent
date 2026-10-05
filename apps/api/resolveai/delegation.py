"""Request-local dispatch deduplication and deterministic finding convergence."""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone

from .request_budget import RequestBudget
from .schemas import DelegationTask, SpecialistFinding


def marker(task: DelegationTask, status: str, reason: str) -> dict:
    return SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status=status,
        queried_at=datetime.now(timezone.utc), unresolved=[reason]).model_dump(mode="json")


def merge_findings(left: list[dict], right: list[dict]) -> list[dict]:
    by_task = {}
    for raw in (left or []) + (right or []):
        parsed = SpecialistFinding.model_validate(raw)
        stamp = parsed.queried_at
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        value = parsed.model_copy(update={"queried_at": stamp}).model_dump(mode="json")
        key = value["plan_revision"], value["task_id"]
        prior = by_task.get(key)
        if prior is None or prior == value:
            by_task[key] = value
            continue
        first, second = SpecialistFinding.model_validate(prior), SpecialistFinding.model_validate(value)
        by_task[key] = SpecialistFinding(task_id=key[1], plan_revision=key[0], status="conflict",
            queried_at=max(first.queried_at, second.queried_at), tool_calls=max(first.tool_calls, second.tool_calls),
            unresolved=["duplicate_task_conflict"]).model_dump(mode="json")
    return [by_task[key] for key in sorted(by_task)]


class TaskRuns:
    """Share one execution per full task contract within one coordinator run."""

    def __init__(self, budget: RequestBudget, *, now=None):
        self.budget = budget
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.records = {}

    def invoke(self, task: DelegationTask, execute) -> dict:
        remaining = min(self.budget.remaining_seconds(), (task.deadline - self.now()).total_seconds())
        if remaining <= 0:
            return marker(task, "error", "deadline_expired")
        key = task.plan_revision, task.task_id
        contract = task.model_dump(mode="json")
        with self.lock:
            record = self.records.get(key)
            owner = record is None
            if owner:
                record = {"task": contract, "event": threading.Event(), "result": None, "error": None}
                self.records[key] = record
            elif record["task"] != contract:
                return marker(task, "conflict", "duplicate_task_conflict")
        if owner:
            try:
                record["result"] = SpecialistFinding.model_validate(execute()).model_dump(mode="json")
            except BaseException as error:
                record["error"] = error
                raise
            finally:
                record["event"].set()
        elif not record["event"].wait(timeout=remaining):
            return marker(task, "error", "deadline_expired")
        self.budget.remaining_seconds()
        if self.now() > task.deadline:
            return marker(task, "error", "deadline_expired")
        if record["error"] is not None:
            raise record["error"]
        return copy.deepcopy(record["result"])
