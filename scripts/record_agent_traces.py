"""Record the knowledge agent's eval traces: one real run per eval case.

    LLM_TIMEOUT_SECONDS=120 npm run agent:record
    (= uv run python -m scripts.record_agent_traces)

Runs every question in `data/eval/agent/eval-cases.json` once through the
production graph (`build_agent_graph`, the real `rag.retrieve` and
`rag.generate_answer`, so Qdrant and both models must be up) and writes the run
traces to `data/eval/agent/traces.json`, together with the graph version and
the model ids that produced them. The evals in
`tests/pipelines/test_agent_evals.py` read only that file, so they run offline
and the same every time; re-record after any change to the graph, the prompt
or the threshold (the evals refuse traces from another graph version).

The runs use a temporary store, not the API's `AGENT_STORE_PATH`. Nothing is
written if any case fails, so a half-recorded file is never committed.

Exit codes: 0 recorded, 1 a run failed, 2 the cases file is invalid or the
models are not configured.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError, get_rag_settings
from services.agent.errors import AgentRunError
from services.agent.graph import build_agent_graph, graph_fingerprint
from services.agent.runner import run_agent
from services.agent.tracing import TraceStore

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT_DIR / "data" / "eval" / "agent" / "eval-cases.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "eval" / "agent" / "traces.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    """The eval cases, checked for the fields the recorder and the evals rely on."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"Every case needs a unique id (got {case_id!r})")
        seen.add(case_id)
        if not isinstance(case.get("question"), str):
            raise ValueError(f"Case {case_id} needs a question")
        if not isinstance(case.get("checks"), dict) or "outcome" not in case["checks"]:
            raise ValueError(f"Case {case_id} needs checks with an expected outcome")
    return cases


def record(cases: list[dict[str, Any]], store_path: Path) -> dict[str, Any]:
    """Run every case through a freshly compiled graph; return the traces document.

    Raises AgentRunError on the first failed run.
    """
    graph = build_agent_graph(InMemorySaver())
    store = TraceStore(store_path)
    recorded = []
    for case in cases:
        result = run_agent(case["question"], graph=graph, store=store)
        trace = store.get_trace(result.run_id)
        assert trace is not None
        recorded.append({"id": case["id"], "question": case["question"], "trace": asdict(trace)})
        print(f"  {case['id']:40} {result.outcome}", file=sys.stderr)
    settings = get_rag_settings()
    return {
        "description": (
            "Recorded knowledge agent runs, one per case in eval-cases.json. Written by "
            "`npm run agent:record`; read by tests/pipelines/test_agent_evals.py."
        ),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph_version": graph_fingerprint(),
        "embedding_model": settings.embedding_model,
        "generation_model": settings.generation_model,
        "min_score": rag.MIN_SCORE,
        "cases": recorded,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.cases)
        settings = get_rag_settings()
        settings.embedding_endpoint()
        settings.generation_endpoint()
    except (OSError, ValueError, KeyError, KnowledgeBaseError) as error:
        print(f"Cannot record: {error}", file=sys.stderr)
        return 2

    print(f"Recording {len(cases)} cases (graph {graph_fingerprint()})", file=sys.stderr)
    with tempfile.TemporaryDirectory() as scratch:
        try:
            document = record(cases, Path(scratch) / "agent_runs.sqlite")
        except AgentRunError as error:
            print(f"A run failed at '{error.node}'; nothing was written.", file=sys.stderr)
            return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
