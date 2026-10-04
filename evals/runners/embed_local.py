"""Small FastEmbed subprocess bridge for the optional policy retrieval experiment."""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")

from fastembed import TextEmbedding


def main() -> None:
    payload = json.load(sys.stdin)
    texts = payload["texts"]
    if not isinstance(texts, list) or len(texts) > 1000 or any(not isinstance(text, str) or len(text) > 2000 for text in texts):
        raise ValueError("Only bounded synthetic text batches are supported")
    model_name = os.environ.get("POLICY_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    if model_name not in {"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "BAAI/bge-small-zh-v1.5"}:
        raise ValueError("Embedding model is not allowlisted for this experiment")
    model = TextEmbedding(model_name=model_name, cache_dir=os.environ.get("HF_HOME"))
    vectors = [[round(float(number), 7) for number in row] for row in model.embed(texts)]
    print(json.dumps({"vectors": vectors, "dimensions": len(vectors[0]) if vectors else 0}))


if __name__ == "__main__":
    main()
