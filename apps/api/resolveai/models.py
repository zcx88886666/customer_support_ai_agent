from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    channel: Mapped[str] = mapped_column(String(32), default="web")
    memory_consent: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    returnable: Mapped[bool] = mapped_column(Boolean, default=True)
    physical: Mapped[bool] = mapped_column(Boolean, default=True)
    special_notice_accepted: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    seller_id: Mapped[str] = mapped_column(String(64))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="paid")
    version: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    policy_bundle_id: Mapped[str] = mapped_column(ForeignKey("policy_bundles.id"))
    favorable_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")
    __table_args__ = (CheckConstraint("version > 0"),)


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    paid_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    refunded_quantity: Mapped[int] = mapped_column(Integer, default=0)
    order: Mapped[Order] = relationship(back_populates="items")
    __table_args__ = (CheckConstraint("quantity > 0"), CheckConstraint("paid_cents >= 0"), CheckConstraint("refunded_cents >= 0"), CheckConstraint("refunded_quantity >= 0"))


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    paid_cents: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(32), default="synthetic_original")
    currency: Mapped[str] = mapped_column(String(3), default="CNY")


class PaidAllocation(Base):
    __tablename__ = "paid_allocations"
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"), primary_key=True)
    paid_cents: Mapped[int] = mapped_column(Integer)


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="in_transit")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PolicyBundle(Base):
    __tablename__ = "policy_bundles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("uq_active_policy", "active", unique=True, sqlite_where=active.is_(True), postgresql_where=active.is_(True)),)


class PolicyClause(Base):
    __tablename__ = "policy_clauses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("policy_bundles.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="return_requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    plan_revision: Mapped[int] = mapped_column(Integer, default=1)
    policy_bundle_id: Mapped[str] = mapped_column(ForeignKey("policy_bundles.id"))
    __table_args__ = (CheckConstraint("quantity > 0"),)


class WarehouseReceipt(Base):
    __tablename__ = "warehouse_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    return_id: Mapped[str] = mapped_column(ForeignKey("return_requests.id"), unique=True)
    quantity: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str] = mapped_column(String(64))


class RefundDeadlineAlert(Base):
    __tablename__ = "refund_deadline_alerts"
    return_id: Mapped[str] = mapped_column(ForeignKey("return_requests.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("warehouse_receipts.id"), unique=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(String(200), default="")
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefundProposal(Base):
    __tablename__ = "refund_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    return_id: Mapped[str] = mapped_column(ForeignKey("return_requests.id"), index=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id"))
    order_version: Mapped[int] = mapped_column(Integer)
    plan_revision: Mapped[int] = mapped_column(Integer)
    policy_bundle_id: Mapped[str] = mapped_column(ForeignKey("policy_bundles.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("amount_cents >= 0"),)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("refund_proposals.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefundLedger(Base):
    __tablename__ = "refund_ledger"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("refund_proposals.id"), unique=True)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    topic: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="open")
    support_actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(24))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ThreadState(Base):
    __tablename__ = "thread_states"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(String(200))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("customer_id", "key"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    before_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_bundle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
