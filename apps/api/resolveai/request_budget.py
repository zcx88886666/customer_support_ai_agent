"""Request-local resource admission shared by parallel read-only branches."""

from __future__ import annotations

import math
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetLimits:
    timeout_seconds: float = 25
    max_llm_calls: int = 10
    max_tokens: int = 16000
    max_cost_usd: float = 0.02

    def __post_init__(self):
        if not 1 <= self.max_llm_calls <= 10 or self.max_tokens < 1:
            raise ValueError("Resource limits require 1–10 calls and positive tokens")
        if any(not math.isfinite(value) or value <= 0 for value in (self.timeout_seconds, self.max_cost_usd)):
            raise ValueError("Resource timeout and cost limits must be finite and positive")

    @classmethod
    def from_environment(cls):
        return cls(timeout_seconds=float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "25")),
                   max_llm_calls=int(os.getenv("AGENT_MAX_LLM_CALLS", "10")),
                   max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "16000")),
                   max_cost_usd=float(os.getenv("AGENT_MAX_COST_USD", "0.02")))


class RequestBudget:
    def __init__(self, limits: BudgetLimits | None = None, *, clock=time.monotonic):
        self.limits = limits or BudgetLimits.from_environment()
        self.clock = clock
        self.started_at = clock()
        self.lock = threading.RLock()
        self.attempts = 0
        self.pending = {}
        self.accounted_tokens = 0
        self.accounted_cost = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.reported_cost = 0.0
        self.unknown_usage_calls = 0
        self.exhausted_reason = None

    def stop(self, reason: str):
        with self.lock:
            self.exhausted_reason = self.exhausted_reason or reason

    def remaining_seconds(self) -> float:
        with self.lock:
            remaining = self.limits.timeout_seconds - (self.clock() - self.started_at)
            if remaining <= 0:
                self.stop("deadline_expired")
            if self.exhausted_reason:
                raise BudgetExceeded(self.exhausted_reason)
            return remaining

    def reserve(self, input_bound: int, output_bound: int, cost_bound: float) -> int:
        if input_bound < 0 or output_bound < 0 or not math.isfinite(cost_bound) or cost_bound < 0:
            raise ValueError("Invalid model resource reservation")
        with self.lock:
            self.remaining_seconds()
            reason = ("llm_call_limit" if self.attempts >= self.limits.max_llm_calls else
                      "token_limit" if self.accounted_tokens + input_bound + output_bound > self.limits.max_tokens else
                      "cost_limit" if self.accounted_cost + cost_bound > self.limits.max_cost_usd + 1e-12 else None)
            if reason:
                self.stop(reason)
                raise BudgetExceeded(reason)
            self.attempts += 1
            self.pending[self.attempts] = (input_bound + output_bound, cost_bound)
            self.accounted_tokens += input_bound + output_bound
            self.accounted_cost += cost_bound
            return self.attempts

    def finish(self, ticket: int, usage: dict):
        with self.lock:
            token_bound, cost_bound = self.pending.pop(ticket)
            incoming, outgoing, cost = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost")
            valid_tokens = all(type(value) is int and value >= 0 for value in (incoming, outgoing))
            valid_cost = type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
            if valid_tokens:
                self.input_tokens += incoming
                self.output_tokens += outgoing
                self.accounted_tokens += incoming + outgoing - token_bound
                if incoming + outgoing > token_bound:
                    self.stop("provider_token_overrun")
            if valid_cost:
                self.reported_cost += cost
                self.accounted_cost += cost - cost_bound
                if cost > cost_bound + 1e-12:
                    self.stop("provider_cost_overrun")
            if not valid_tokens or not valid_cost:
                self.unknown_usage_calls += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {"llm_attempts": self.attempts, "reported_input_tokens": self.input_tokens,
                    "reported_output_tokens": self.output_tokens, "reported_cost_usd": round(self.reported_cost, 10),
                    "accounted_tokens": self.accounted_tokens, "accounted_cost_usd": round(self.accounted_cost, 10),
                    "unknown_usage_calls": self.unknown_usage_calls, "pending_calls": len(self.pending),
                    "elapsed_seconds": round(self.clock() - self.started_at, 4), "exhausted_reason": self.exhausted_reason,
                    "limits": {"timeout_seconds": self.limits.timeout_seconds, "max_llm_calls": self.limits.max_llm_calls,
                               "max_tokens": self.limits.max_tokens, "max_cost_usd": self.limits.max_cost_usd}}


_budget: ContextVar[RequestBudget | None] = ContextVar("resolveai_request_budget", default=None)
_branch_deadline: ContextVar[float | None] = ContextVar("resolveai_branch_deadline", default=None)


def current_budget() -> RequestBudget | None:
    return _budget.get()


def remaining_io_seconds(maximum: float) -> float:
    budget = current_budget()
    if budget is None:
        return maximum
    remaining = min(maximum, budget.remaining_seconds())
    deadline = _branch_deadline.get()
    if deadline is not None:
        remaining = min(remaining, deadline - budget.clock())
    if remaining <= 0:
        raise BudgetExceeded("specialist_deadline_expired")
    return remaining


@contextmanager
def budget_scope(budget: RequestBudget, *, timeout_seconds: float | None = None):
    token = _budget.set(budget)
    deadline_token = None
    if timeout_seconds is not None:
        deadline = budget.clock() + timeout_seconds
        inherited = _branch_deadline.get()
        deadline_token = _branch_deadline.set(min(deadline, inherited) if inherited is not None else deadline)
    try:
        yield budget
    finally:
        if deadline_token is not None:
            _branch_deadline.reset(deadline_token)
        _budget.reset(token)
