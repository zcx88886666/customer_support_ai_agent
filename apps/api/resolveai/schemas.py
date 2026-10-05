from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReturnInput(BaseModel):
    order_id: str
    order_item_id: str
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=200)
    confirmed: bool
    idempotency_key: str = Field(min_length=1, max_length=100)
    plan_revision: int = Field(default=1, ge=1, le=4)


class ReceiptInput(BaseModel):
    quantity: int = Field(gt=0)
    received_at: datetime | None = None


class InspectionInput(BaseModel):
    passed: bool
    note: str = Field(default="", max_length=200)


class DecisionInput(BaseModel):
    approve: bool


class IssueInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)


class PolicyClauseInput(BaseModel):
    id: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=10, max_length=5000)


class PolicyDraftInput(BaseModel):
    id: str = Field(min_length=3, max_length=64)
    effective_from: datetime
    window_days: int = Field(ge=7, le=30)
    clauses: list[PolicyClauseInput] = Field(min_length=1)


class ConsentInput(BaseModel):
    consent: bool


class PreferenceInput(BaseModel):
    value: str = Field(min_length=1, max_length=120)
    confirmed: bool


class TicketAssignInput(BaseModel):
    support_actor_id: str = Field(min_length=1, max_length=64)


class ChatInput(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    order_id: str | None = None
    shipment_id: str | None = None
    item_id: str | None = None
    quantity: int | None = None
    reason: str | None = None
    confirmed: bool = False
    idempotency_key: str | None = None
    agent_mode: Literal["single", "collab"] = "single"


class RouteDecision(BaseModel):
    route: Literal["knowledge", "after_sales", "clarify", "human_handoff", "out_of_scope"]
    intents: list[Literal["policy_qa", "order_status", "shipment_tracking", "cancel_request", "return_request", "refund_request", "complaint", "human_request", "unknown"]] = Field(max_length=3)
    uncertainty: str | None = None


class DelegationTask(BaseModel):
    task_id: str
    thread_id: str
    plan_revision: int
    specialist: Literal["policy", "order"]
    question_scope: str
    verified_order_ref: str | None = None
    verified_shipment_ref: str | None = None
    policy_bundle_id: str | None = None
    evidence_version_hint: int | None = None
    deadline: datetime
    order_read_scope: Literal["status", "shipment"] = "shipment"


class OrderToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[Literal["get_order", "track_shipment"]] = Field(max_length=2)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Repeated tools are not allowed")
        return value


class PolicyRetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(max_length=2)

    @field_validator("queries")
    @classmethod
    def bounded_unique_queries(cls, value):
        value = [query.strip() for query in value]
        if any(not query or len(query) > 200 for query in value) or len(value) != len(set(value)):
            raise ValueError("Queries must be nonempty, unique, and at most 200 characters")
        return value


class SpecialistFinding(BaseModel):
    task_id: str
    plan_revision: int
    status: Literal["ok", "incomplete", "conflict", "error"]
    facts: dict = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    source_version: str | None = None
    queried_at: datetime
    unresolved: list[str] = Field(default_factory=list)
    tool_calls: int = Field(default=0, le=2)
    model_reviewed: bool = False
    reviewed_source_ids: list[str] = Field(default_factory=list)


class SpecialistReview(BaseModel):
    """Untrusted model ranking of already verified, aliased evidence."""

    selected_evidence: list[str]
    unresolved_conditions: list[str]
