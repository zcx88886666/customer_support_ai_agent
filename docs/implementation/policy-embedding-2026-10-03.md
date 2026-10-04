# Local semantic policy retrieval experiment — 2026-10-03

The [optional runner](../../evals/runners/run_policy_embedding.py) evaluated the live character-gram retrieval algorithm and two local FastEmbed candidates on an original synthetic development corpus. Four demo clauses plus [36 additional after-sales clauses](../../evals/datasets/policy_retrieval_expansion_v1.jsonl) formed a 40-clause bundle. [The query set](../../evals/datasets/policy_retrieval_v1.jsonl) and [near-domain negatives](../../evals/datasets/policy_retrieval_negatives_v1.jsonl) provided 56 relevant questions and 25 questions with no supported policy answer. Clause and query vectors were computed locally, with zero provider calls. The extra clauses and questions were authored for this experiment, not approved or published as customer policy.

| Retrieval candidate | Hit@1 | Hit@3 | Hit@5 on 56 relevant questions | Abstained on 25 negatives |
|---|---:|---:|---:|---:|
| Current character-gram retrieval | 15 | 24 | 26 | 12 |
| Multilingual MiniLM, pure vector rank | 28 | 45 | 52 | 9 at a 0.30 similarity cutoff |
| Multilingual MiniLM + lexical reciprocal-rank fusion | 29 | 41 | 49 | No independent abstention rule |
| Chinese BGE-small, pure vector rank | 32 | 41 | 44 | 1 at the same 0.30 cutoff |
| Chinese BGE-small + lexical reciprocal-rank fusion | 27 | 36 | 45 | No independent abstention rule |

The multilingual model was `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions); the Chinese candidate was `BAAI/bge-small-zh-v1.5` (512 dimensions). Both are listed by [FastEmbed's supported-model catalog](https://qdrant.github.io/fastembed/examples/Supported_Models/); the [multilingual model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) describes semantic search use. The committed runner allowlists these exact IDs. Detailed synthetic results and dataset hashes are under ignored `evals/reports/20261004T005857Z-policy-embedding-63e81f/` for multilingual MiniLM and `evals/reports/20261004T005950Z-policy-embedding-f6e412/` for Chinese BGE-small.

The multilingual candidate exceeded the planned 85% Hit@5 target on this development set (52/56, 92.9%), but a 0.30 cutoff still accepted 16/25 unrelated or adjacent questions. That cutoff appeared to separate the first five easy negatives; the additional twenty near-domain negatives exposed the mistake. Chinese BGE-small had lower Hit@5 and accepted 24/25 negatives at the same cutoff. Character grams also returned some irrelevant near-domain clauses. Local embedding batch times were 1.58 seconds for cached multilingual MiniLM and 11.38 seconds for the first Chinese BGE-small run, which included model download and cannot be treated as a warmed latency comparison. The authored gold has not had the planned two-person review, and some questions overlap neighboring clauses. These numbers do not establish a production release gate.

The live PostgreSQL policy index remains the existing version-scoped full-text/character-gram path. Semantic vectors were **not** added to the runtime database or Docker API because neither tested candidate had a safe abstention rule on the broader negative set. The next gate is a human-reviewed, disjoint policy corpus and an independently evaluated relevance/abstention rule, followed by version-scoped PostgreSQL integration. The business rule service continues to decide eligibility independently of retrieval.

Reproduce with an optional local environment containing `fastembed==0.8.1`:

```bash
FASTEMBED_PYTHON=/path/to/fastembed-venv/bin/python HF_HOME=/path/to/model-cache .venv/bin/python evals/runners/run_policy_embedding.py
FASTEMBED_PYTHON=/path/to/fastembed-venv/bin/python HF_HOME=/path/to/model-cache POLICY_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5 .venv/bin/python evals/runners/run_policy_embedding.py
```

The no-key API and Docker stack do not install or download this optional model. All question text and source clauses in the experiment are synthetic and versioned in this repository; reports and model weights are ignored by Git.
