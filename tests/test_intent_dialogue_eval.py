from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evals.runners import run_intent_dialogue


@pytest.mark.parametrize("case", run_intent_dialogue.load_cases(), ids=lambda case: case["case_id"])
def test_intent_dialogue_state_and_safety(case):
    result = run_intent_dialogue.run_case(case, "intent-dialogue-test", datetime.now(timezone.utc))
    assert result["status"] == "pass", result
    assert all(result["checks"].values())
