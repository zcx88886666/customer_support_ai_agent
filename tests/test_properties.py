from __future__ import annotations

from hypothesis import given, settings, strategies as st

from resolveai.domain import expected_refund
from resolveai.models import OrderItem


@settings(max_examples=1000, deadline=None)
@given(paid=st.integers(min_value=0, max_value=100_000_000), quantity=st.integers(min_value=1, max_value=100))
def test_all_partial_refunds_sum_to_paid(paid: int, quantity: int):
    item = OrderItem(id="property-item", order_id="property-order", product_id="property-product", quantity=quantity, paid_cents=paid, refunded_cents=0, refunded_quantity=0)
    amounts = []
    for _ in range(quantity):
        amount = expected_refund(item, 1)
        assert amount >= 0
        amounts.append(amount)
        item.refunded_cents += amount
        item.refunded_quantity += 1
    assert sum(amounts) == paid
    assert item.refunded_cents == paid
