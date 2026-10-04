"""Generate the fixed, synthetic multi-session memory A/B stories."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals/datasets/memory_stories_v1.jsonl"

PAIRS = [
    ("language", "English", "Spanish", "Which language should my replies use?"),
    ("language", "French", "German", "Which language should my replies use?"),
    ("language", "Japanese", "Korean", "Which language should my replies use?"),
    ("language", "Arabic", "Portuguese", "Which language should my replies use?"),
    ("channel", "email", "live chat", "Which contact channel do I prefer?"),
    ("channel", "SMS", "phone", "Which contact channel do I prefer?"),
    ("communication_style", "concise bullet points", "detailed paragraphs", "How should replies be written?"),
    ("communication_style", "plain language", "step-by-step explanations", "How should replies be written?"),
    ("language", "Italian", "Mandarin", "Which language should my replies use?"),
    ("channel", "live chat", "email", "Which contact channel do I prefer?"),
]


def build() -> list[dict]:
    stories = []
    for group, action in enumerate(("retain", "correct", "revoke", "no_consent")):
        for index, (key, initial, replacement, query) in enumerate(PAIRS):
            number = group * 10 + index + 1
            stories.append(
                {
                    "case_id": f"memory-{number:03d}",
                    "customer_id": f"synthetic_memory_user_{number:03d}",
                    "action": action,
                    "key": key,
                    "initial": initial,
                    "replacement": replacement if action == "correct" else None,
                    "query": query,
                    "sessions": [
                        {"confirmed": action != "no_consent", "consent": action != "no_consent", "utterance": f"I confirm that I prefer {initial} for {key.replace('_', ' ')}."},
                        {"confirmed": action == "correct", "consent": action != "no_consent", "utterance": f"I now prefer {replacement} for {key.replace('_', ' ')}." if action == "correct" else "Please remember my current preference." if action == "retain" else "I withdraw my memory consent." if action == "revoke" else "Do not save this without my consent."},
                    ],
                    "expected": replacement if action == "correct" else initial if action == "retain" else None,
                }
            )
    return stories


if __name__ == "__main__":
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in build()), encoding="utf-8")
    print(f"Wrote {len(build())} synthetic stories to {OUTPUT}")
