"""Measure the memory recall threshold: how memories score against related and unrelated questions.

    npm run agent:memory:calibrate   (uv run python -m scripts.calibrate_memory_recall)

Embeds three memories like the ones the agent stores (a carrier rule, a recurring
incident, a client report preference) with the real embeddings model, then scores
them against questions that each memory should answer and against questions it
should not (the RAG test queries, ticket and stock questions, closings). A
threshold between the lowest related score and the highest unrelated one
separates them; `services/agent/memory/store.py::MIN_MEMORY_SCORE` records the
measurement. Needs the embeddings model (`RagSettings`); writes nothing.
"""

import json
import sys
from pathlib import Path

from data.process.knowledge_base import KnowledgeBaseError, embed
from services.agent.memory.store import MIN_MEMORY_SCORE, cosine

ROOT_DIR = Path(__file__).resolve().parents[1]
TEST_QUERIES = ROOT_DIR / "data" / "eval" / "test-queries.json"

MEMORIES = {
    "carrier_rule": (
        "The user reports that, since October 2026, SEUR no longer covers the rural area of "
        "Zaragoza and a local last-mile carrier has been used there instead."
    ),
    "recurring_incident": (
        "The user reports that recurring delays in Los Angeles incidents this week are caused "
        "by the port strike, not by TrackFlow."
    ),
    "client_report_preference": (
        "The cosmetics client wants its monthly report to present the returns breakdown "
        "before shipment volume."
    ),
}
RELATED = {
    "carrier_rule": [
        "Which carrier should we use for deliveries to rural areas of Zaragoza?",
        "Does SEUR still deliver to rural Zaragoza?",
        "Who covers the countryside around Zaragoza now?",
        "Which carrier covers rural Aragón?",
    ],
    "recurring_incident": [
        "Why are the Los Angeles shipments delayed this week?",
        "Should I escalate the delays in Los Angeles?",
        "Is there a known cause for the LA delays?",
    ],
    "client_report_preference": [
        "How does the cosmetics client want its monthly report?",
        "What should go first in the cosmetics brand's monthly report?",
        "Any preferences for the cosmetics client's report?",
    ],
}
# A test query that asks exactly what the carrier-rule memory answers.
ALSO_RELATED = {"Which carrier best covers rural Aragón?": "carrier_rule"}
OTHER_QUESTIONS = [
    "What's the status of ticket 12?",
    "Do we have stock of the black running shoes?",
    "Great, that's resolved.",
    "Translate this into English for the client.",
]


def main() -> int:
    data = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("queries") or data.get("questions")
    test_queries = [entry.get("question") or entry.get("query") for entry in entries]
    try:
        vectors = {key: embed(text) for key, text in MEMORIES.items()}
        related: list[tuple[float, str, str]] = []
        unrelated: list[tuple[float, str, str]] = []
        for key, questions in RELATED.items():
            for question in questions:
                vector = embed(question)
                for other, memory in vectors.items():
                    pair = (round(cosine(vector, memory), 4), other, question)
                    (related if other == key else unrelated).append(pair)
        for question in [*test_queries, *OTHER_QUESTIONS]:
            vector = embed(question)
            for other, memory in vectors.items():
                pair = (round(cosine(vector, memory), 4), other, question)
                (related if ALSO_RELATED.get(question) == other else unrelated).append(pair)
    except KnowledgeBaseError as error:
        print(f"Cannot calibrate: {error}", file=sys.stderr)
        return 2
    related.sort()
    unrelated.sort(reverse=True)
    print("Related pairs, lowest first:")
    for pair in related:
        print(f"  {pair[0]:.4f}  {pair[1]:26} {pair[2]}")
    print("Unrelated pairs, highest first (top 10):")
    for pair in unrelated[:10]:
        print(f"  {pair[0]:.4f}  {pair[1]:26} {pair[2]}")
    low, high = related[0][0], unrelated[0][0]
    print(
        f"\nlowest related {low:.4f} · highest unrelated {high:.4f} · "
        f"{'separable' if low > high else 'overlapping'} · "
        f"MIN_MEMORY_SCORE {MIN_MEMORY_SCORE} "
        f"({'inside the gap' if high < MIN_MEMORY_SCORE <= low else 'OUTSIDE the gap'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
