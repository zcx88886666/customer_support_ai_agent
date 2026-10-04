from evals.runners.run_memory_ab import score, stories


def test_memory_story_dataset_has_four_two_session_groups():
    cases = stories()
    assert len(cases) == 40
    assert {action: sum(case["action"] == action for case in cases) for action in ("retain", "correct", "revoke", "no_consent")} == {"retain": 10, "correct": 10, "revoke": 10, "no_consent": 10}
    assert all(len(case["sessions"]) == 2 for case in cases)


def test_memory_scorer_rejects_stale_correction_and_foreign_result():
    case = next(case for case in stories() if case["action"] == "correct")
    result = score([case], "fixture", {case["case_id"]: 1.0}, lambda _: [(case["initial"], case["customer_id"]), (case["replacement"], case["customer_id"])], lambda _: [(case["replacement"], "foreign_customer")], lambda _: [], {})
    assert result["fail"] == 1
    assert result["results"][0]["old_removed"] is False
    assert result["results"][0]["isolation_ok"] is False


def test_memory_scorer_rejects_nonempty_revoked_result():
    case = next(case for case in stories() if case["action"] == "revoke")
    result = score([case], "fixture", {case["case_id"]: 1.0}, lambda _: [case["initial"]], lambda _: [], lambda _: [], {})
    assert result["fail"] == 1
    assert result["results"][0]["empty_after_revoke"] is False
