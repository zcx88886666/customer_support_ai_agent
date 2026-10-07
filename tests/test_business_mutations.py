from evals.runners.run_business_mutations import run_mutants


def test_targeted_business_mutations_are_detected():
    result = run_mutants()
    assert result["counts"] == {"killed": 17, "survived": 0, "invalid": 0}
    assert result["mutation_score"] == 1.0
    review_cases = {row["mutation"]: row for row in result["results"]
                    if row["mutation"].startswith("review_")}
    assert set(review_cases) == {"review_audit_wrong_actor", "review_ticket_wrong_order"}
    assert all("chat_review_ticket" in row["failed_checks"] for row in review_cases.values())
    late_approval = next(row for row in result["results"]
                         if row["mutation"] == "approval_timestamp_after_refund")
    assert "refund_authorized" in late_approval["failed_checks"]
    wrong_approver_audit = next(row for row in result["results"]
                                if row["mutation"] == "approval_audit_wrong_actor")
    assert "refund_authorized" in wrong_approver_audit["failed_checks"]
    wrong_refund_version = next(row for row in result["results"]
                                if row["mutation"] == "refund_audit_wrong_version")
    assert "refund_authorized" in wrong_refund_version["failed_checks"]
    wrong_item_balance = next(row for row in result["results"]
                              if row["mutation"] == "refund_item_balance_extra_cent")
    assert {"refund_authorized", "refund_balance"} <= set(wrong_item_balance["failed_checks"])
    denied_balance = next(row for row in result["results"]
                          if row["mutation"] == "denied_return_has_refund_balance")
    assert "refund_balance" in denied_balance["failed_checks"]
    foreign_balance = next(row for row in result["results"]
                           if row["mutation"] == "other_order_has_refund_balance")
    assert "refund_balance" in foreign_balance["failed_checks"]
