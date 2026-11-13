"""Recall@3 of the TrackFlow knowledge base, and the evidence for its similarity threshold.

    uv run python -m scripts.evaluate_rag_retrieval      (or: npm run rag:eval)

Runs every question in `data/eval/test-queries.json` through the production
`retrieve()` (same `embed()`, same collection) and checks whether the expected
chunk is among the top 3 (BRIEFING section 4: at least 80%). Recall@3 is
reported twice: on the ranking alone, and counting only hits that also clear
`MIN_SCORE`, which is what `query()` actually sees. The second one is the gate.

The out-of-scope questions measure how high an unrelated question scores, so
the threshold can sit between them and the lowest correct hit.

Needs the indexed collection (`python -m data.process.knowledge_base`) and the
embeddings model. Writes `data/eval/rag/retrieval_eval.json`.

Exit codes: 0 target met, 1 below target, 2 the test set is invalid or Qdrant /
the embeddings model is unavailable.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from data.pipelines import rag
from data.process.knowledge_base import (
    SOURCE_DOCUMENTS,
    KnowledgeBaseError,
    get_rag_settings,
    normalise_for_embedding,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = ROOT_DIR / "data" / "eval" / "test-queries.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "eval" / "rag" / "retrieval_eval.json"
MIN_QUESTIONS = 8  # BRIEFING section 5

Search = Callable[[str], list[dict[str, Any]]]


class TestSetError(ValueError):
    """`test-queries.json` does not meet BRIEFING section 5."""


def load_test_queries(path: Path = DEFAULT_QUERIES) -> dict[str, Any]:
    """Read the test set and check it covers what BRIEFING section 5 asks for."""
    test_set = json.loads(path.read_text(encoding="utf-8"))
    queries = test_set.get("queries", [])
    ids = [query["id"] for query in queries] + [q["id"] for q in test_set.get("out_of_scope", [])]
    if len(queries) < MIN_QUESTIONS:
        raise TestSetError(f"{len(queries)} questions; BRIEFING asks for at least {MIN_QUESTIONS}")
    if len(set(ids)) != len(ids):
        raise TestSetError("question ids must be unique")
    covered = {query["expected"]["source_document"] for query in queries}
    missing = set(SOURCE_DOCUMENTS.values()) - covered
    if missing:
        raise TestSetError(f"no question covers {sorted(missing)}")
    if not any("peak" in query.get("tags", []) for query in queries):
        raise TestSetError("no question about high-demand peaks (Black Friday / Sales)")
    return test_set


def is_expected_chunk(hit: dict[str, Any], expected: dict[str, str]) -> bool:
    return hit.get("source_document") == expected["source_document"] and normalise_for_embedding(
        expected["text_contains"]
    ) in normalise_for_embedding(str(hit.get("text", "")))


def score_query(query: dict[str, Any], hits: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """Where the expected chunk ranks among the top `k` hits, and its score."""
    top = hits[:k]
    rank = next(
        (
            position
            for position, hit in enumerate(top, 1)
            if is_expected_chunk(hit, query["expected"])
        ),
        None,
    )
    return {
        "id": query["id"],
        "question": query["question"],
        "tags": query.get("tags", []),
        "expected_source_document": query["expected"]["source_document"],
        "rank": rank,
        "correct_score": top[rank - 1]["score"] if rank else None,
        "top": [
            {
                "source_document": hit.get("source_document"),
                "chunk_index": hit.get("chunk_index"),
                "score": round(hit["score"], 4),
            }
            for hit in top
        ],
    }


def suggest_threshold(
    correct_scores: list[float], out_of_scope_scores: list[float]
) -> dict[str, Any]:
    """Suggest a `MIN_SCORE` from the worst correct score and the best unrelated one.

    Separable (every unrelated question scores below every correct chunk): the
    midpoint of the gap, rounded down to 2 decimals. Overlapping: no value can
    reject the unrelated questions without also dropping correct chunks, so the
    suggestion is recall-first, the worst correct score rounded down to a 0.05
    step. Unrelated passages that get through are left to the prompt, which
    tells the model to say when the context does not answer the question.
    """
    if not correct_scores or not out_of_scope_scores:
        return {"separable": None, "policy": None, "suggested": None}
    lowest_correct = min(correct_scores)
    highest_unrelated = max(out_of_scope_scores)
    separable = highest_unrelated < lowest_correct
    return {
        "lowest_correct_score": round(lowest_correct, 4),
        "highest_out_of_scope_score": round(highest_unrelated, 4),
        "separable": separable,
        "policy": "midpoint" if separable else "recall-first",
        "suggested": int((highest_unrelated + lowest_correct) / 2 * 100) / 100
        if separable
        # round() first so a score exactly on a step (0.35 * 20 = 6.999...) keeps it.
        else math.floor(round(lowest_correct * 20, 9)) / 20,
    }


def evaluate(test_set: dict[str, Any], search: Search, *, min_score: float) -> dict[str, Any]:
    """Score every question with `search` (raw ranking, no threshold) and summarise."""
    k = int(test_set.get("k", 3))
    results = [score_query(query, search(query["question"]), k) for query in test_set["queries"]]
    found = [result for result in results if result["rank"] is not None]
    above = [result for result in found if result["correct_score"] >= min_score]
    out_of_scope = []
    for item in test_set.get("out_of_scope", []):
        hits = search(item["question"])
        best = hits[0] if hits else None
        out_of_scope.append(
            {
                "id": item["id"],
                "question": item["question"],
                "top_score": round(best["score"], 4) if best else None,
                "top_source_document": best.get("source_document") if best else None,
                "passes_threshold": bool(best and best["score"] >= min_score),
            }
        )
    total = len(results)
    return {
        "k": k,
        "min_score": min_score,
        "target_recall_at_k": test_set.get("target_recall_at_k", 0.8),
        "questions": total,
        "recall_at_k_ranking": round(len(found) / total, 4),
        "recall_at_k_with_threshold": round(len(above) / total, 4),
        "misses": [result["id"] for result in results if result not in above],
        "threshold": suggest_threshold(
            [result["correct_score"] for result in found],
            [item["top_score"] for item in out_of_scope if item["top_score"] is not None],
        ),
        "queries": results,
        "out_of_scope": out_of_scope,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        test_set = load_test_queries(args.queries)
        k = int(test_set.get("k", 3))
        report = evaluate(
            test_set,
            lambda question: rag.retrieve(question, k=k, min_score=0.0),
            min_score=rag.MIN_SCORE,
        )
        embedding_model = get_rag_settings().embedding_endpoint()[0]
    except (TestSetError, KnowledgeBaseError) as error:
        print(f"Evaluation could not run: {error}")
        return 2

    report = {
        "embedding_model": embedding_model,
        "collection": test_set.get("collection"),
        **report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    threshold = report["threshold"]
    print(f"Embedding model: {embedding_model}")
    print(f"Recall@{k} (ranking only):           {report['recall_at_k_ranking']:.0%}")
    print(
        f"Recall@{k} (with MIN_SCORE {rag.MIN_SCORE:.2f}):   {report['recall_at_k_with_threshold']:.0%}"
    )
    print(f"Misses: {', '.join(report['misses']) or 'none'}")
    print(
        f"Lowest correct score {threshold.get('lowest_correct_score')}, highest out-of-scope "
        f"score {threshold.get('highest_out_of_scope_score')}, suggested threshold "
        f"{threshold.get('suggested')} ({threshold.get('policy')})"
    )
    print(
        f"Written to {args.output.relative_to(ROOT_DIR) if args.output.is_relative_to(ROOT_DIR) else args.output}"
    )
    return 0 if report["recall_at_k_with_threshold"] >= report["target_recall_at_k"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
