"""Phase 5b: the retrieval test set and the Recall@3 scoring, without Qdrant or a model."""

import json
from typing import Any

import pytest

from data.process.knowledge_base import (
    SOURCE_DOCUMENTS,
    load_source_documents,
    normalise_for_embedding,
    split_into_chunks,
)
from scripts import evaluate_rag_retrieval as ev


def shipped_chunks() -> list[dict[str, Any]]:
    return [chunk.payload() for doc in load_source_documents() for chunk in split_into_chunks(doc)]


def test_shipped_test_set_meets_the_briefing():
    test_set = ev.load_test_queries()

    assert len(test_set["queries"]) >= 8
    assert {q["expected"]["source_document"] for q in test_set["queries"]} == set(
        SOURCE_DOCUMENTS.values()
    )
    peak = [q for q in test_set["queries"] if "peak" in q["tags"]]
    assert any("Black Friday" in q["question"] for q in peak)
    assert test_set["k"] == 3 and test_set["target_recall_at_k"] == 0.8


def test_every_expected_phrase_points_at_exactly_one_shipped_chunk():
    # Guards against a typo in the test set, and against a chunking change that
    # would split a phrase across two chunks (the question could then never hit).
    chunks = shipped_chunks()
    for query in ev.load_test_queries()["queries"]:
        matches = [chunk for chunk in chunks if ev.is_expected_chunk(chunk, query["expected"])]
        assert len(matches) == 1, query["id"]


def test_out_of_scope_questions_name_nothing_in_the_corpus():
    corpus = normalise_for_embedding(" ".join(chunk["text"] for chunk in shipped_chunks())).lower()
    for item in ev.load_test_queries()["out_of_scope"]:
        assert item["question"].lower() not in corpus


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s.update(queries=s["queries"][:7]), "at least 8"),
        (
            lambda s: s.update(
                queries=[
                    q for q in s["queries"] if q["expected"]["source_document"] != "storage-pricing"
                ]
            ),
            "storage-pricing",
        ),
        (lambda s: [q.update(tags=[]) for q in s["queries"]], "peaks"),
        (lambda s: s["out_of_scope"].append({"id": "sla-01", "question": "dup"}), "unique"),
    ],
)
def test_an_incomplete_test_set_is_refused(tmp_path, mutate, message):
    test_set = json.loads(ev.DEFAULT_QUERIES.read_text(encoding="utf-8"))
    mutate(test_set)
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(test_set), encoding="utf-8")

    with pytest.raises(ev.TestSetError, match=message):
        ev.load_test_queries(path)


EXPECTED = {
    "source_document": "carrier-coverage",
    "text_contains": "SEUR: best coverage in rural areas",
}


def hit(source: str, text: str, score: float, index: int = 0) -> dict[str, Any]:
    return {"source_document": source, "chunk_index": index, "text": text, "score": score}


def test_rank_and_score_of_the_expected_chunk():
    hits = [
        hit("sla-delivery", "Delivery SLA ...", 0.71),
        hit(
            "carrier-coverage",
            "Carrier Coverage\nSEUR: best coverage\nin rural areas of Aragón.",
            0.64,
            1,
        ),
    ]

    result = ev.score_query({"id": "car-01", "question": "?", "expected": EXPECTED}, hits, k=3)

    assert result["rank"] == 2
    assert result["correct_score"] == 0.64


def test_a_correct_chunk_beyond_k_is_a_miss():
    hits = [
        hit("sla-delivery", "x", 0.9),
        hit("sla-delivery", "y", 0.8),
        hit("storage-pricing", "z", 0.7),
    ]
    hits.append(hit("carrier-coverage", "SEUR: best coverage in rural areas of Aragón.", 0.6))

    result = ev.score_query({"id": "car-01", "question": "?", "expected": EXPECTED}, hits, k=3)

    assert result["rank"] is None
    assert len(result["top"]) == 3


def test_the_right_phrase_in_the_wrong_document_is_a_miss():
    hits = [hit("storage-pricing", "SEUR: best coverage in rural areas of Aragón.", 0.9)]

    assert (
        ev.score_query({"id": "x", "question": "?", "expected": EXPECTED}, hits, k=3)["rank"]
        is None
    )


def test_recall_with_threshold_drops_correct_hits_below_min_score():
    test_set = {
        "k": 3,
        "target_recall_at_k": 0.8,
        "queries": [
            {"id": "a", "question": "qa", "expected": EXPECTED},
            {"id": "b", "question": "qb", "expected": EXPECTED},
        ],
        "out_of_scope": [{"id": "o", "question": "qo"}],
    }
    scores = {"qa": 0.82, "qb": 0.41, "qo": 0.22}

    def search(question: str) -> list[dict[str, Any]]:
        return [
            hit(
                "carrier-coverage",
                "SEUR: best coverage in rural areas of Aragón.",
                scores[question],
            )
        ]

    report = ev.evaluate(test_set, search, min_score=0.5)

    assert report["recall_at_k_ranking"] == 1.0
    assert report["recall_at_k_with_threshold"] == 0.5
    assert report["misses"] == ["b"]
    assert report["out_of_scope"][0]["passes_threshold"] is False
    assert report["threshold"] == {
        "lowest_correct_score": 0.41,
        "highest_out_of_scope_score": 0.22,
        "separable": True,
        "policy": "midpoint",
        "suggested": 0.31,
    }


def test_overlapping_scores_get_a_recall_first_threshold():
    # No value rejects 0.45 without also dropping the correct 0.429: keep every
    # correct chunk, rounding its worst score down to a 0.05 step.
    result = ev.suggest_threshold([0.429, 0.70], [0.45, 0.10])

    assert result["separable"] is False
    assert result["policy"] == "recall-first"
    assert result["suggested"] == 0.40
    assert result["suggested"] <= min([0.429, 0.70])


def test_recall_first_threshold_on_an_exact_step_keeps_that_score():
    assert ev.suggest_threshold([0.35, 0.9], [0.6])["suggested"] == 0.35


def test_no_threshold_without_scores():
    assert ev.suggest_threshold([], [0.3]) == {"separable": None, "policy": None, "suggested": None}
