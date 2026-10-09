from evals.runners.run_intent_dialogue_live import run_cases, summarize


class FakeMeter:
    costs = iter(())

    def __enter__(self):
        self.cost, self.missing = next(self.costs)
        return self

    def __exit__(self, *_args):
        return False

    def summary(self):
        return {"calls": 1, "reported_cost_usd": self.cost, "cost_missing_calls": self.missing,
                "http_errors": 0, "input_tokens": 1, "output_tokens": 1}


def test_live_cost_stop_marks_remaining_cases_incomplete_without_calling_them():
    cases = [{"case_id": name, "risk_tier": "critical"} for name in ("a", "b", "c")]
    called = []
    FakeMeter.costs = iter(((0.006, 0), (0.006, 0)))

    def replay(case, *_args):
        called.append(case["case_id"])
        return {"case_id": case["case_id"], "risk_tier": "critical", "status": "pass"}

    rows, reason = run_cases(cases, "test", None, 0.01, replay=replay, meter_factory=FakeMeter)
    assert called == ["a", "b"]
    assert [row["status"] for row in rows] == ["pass", "pass", "incomplete"]
    assert rows[2]["error"] == "provider_cost_stop"
    assert reason == "provider_cost_stop"
    assert summarize(cases, rows, full_scope=True, stop_reason=reason)["development_pass"] is False


def test_missing_provider_cost_stops_replay_and_cannot_pass():
    cases = [{"case_id": name, "risk_tier": "critical"} for name in ("a", "b")]
    called = []
    FakeMeter.costs = iter(((0.0, 1),))

    def replay(case, *_args):
        called.append(case["case_id"])
        return {"case_id": case["case_id"], "risk_tier": "critical", "status": "pass"}

    rows, reason = run_cases(cases, "test", None, 0.01, replay=replay, meter_factory=FakeMeter)
    assert called == ["a"]
    assert rows[1]["status"] == "incomplete"
    assert reason == "provider_cost_unreported"
    summary = summarize(cases, rows, full_scope=True, stop_reason=reason)
    assert summary["development_pass"] is False
    assert summary["release_gate_pass"] is False
