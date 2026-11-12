"""Record the knowledge agent's eval traces: one real run per eval case.

    LLM_TIMEOUT_SECONDS=120 npm run agent:record
    (= uv run python -m scripts.record_agent_traces)

Runs every question in `data/eval/agent/eval-cases.json` once through the
production graph (`build_agent_graph`, the real planner, `rag.retrieve`,
`rag.generate_answer` and tools) and writes the run traces to
`data/eval/agent/traces.json`, together with the graph version, the model ids
and the tool timeout that produced them. Qdrant, both models, and the incident
and inventory managers (`INCIDENTS_API_URL`, `INVENTORY_API_URL`, the
`AGENT_INVENTORY_*` account) must be up: the cases ask about real tickets and
products. The evals in `tests/pipelines/test_agent_evals.py` read only that
file, so they run offline and the same every time; re-record after any change
to the graph, a prompt, the threshold or a tool schema (the evals refuse traces
from another graph version).

A case with `"services": {"incident_manager": "unreachable"}` is recorded with
that service's URL pointed at a closed local port, for that case only: a real
connection failure, so the fallback path is recorded as it happens.

The runs use a temporary store, not the API's `AGENT_STORE_PATH`. Nothing is
written if any case fails, so a half-recorded file is never committed.

Exit codes: 0 recorded, 1 a run failed, 2 the cases file is invalid, the
models are not configured, or a service the cases need is not reachable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError, get_rag_settings
from services.agent.config import get_agent_settings
from services.agent.errors import AgentRunError
from services.agent.graph import build_agent_graph, graph_fingerprint
from services.agent.runner import run_agent
from services.agent.state import INCIDENT_MANAGER, INVENTORY_MANAGER, NODE_OF_SOURCE
from services.agent.tools import incidents, inventory
from services.agent.tools.contracts import StockLookupInput, TicketLookupInput
from services.agent.tracing import TraceStore

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT_DIR / "data" / "eval" / "agent" / "eval-cases.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "eval" / "agent" / "traces.json"

# Port 9 (discard) is closed on a developer machine: a connection is refused at once.
UNREACHABLE_URL = "http://127.0.0.1:9"
URL_SETTING = {INCIDENT_MANAGER: "INCIDENTS_API_URL", INVENTORY_MANAGER: "INVENTORY_API_URL"}


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
        for source, state in (case.get("services") or {}).items():
            if source not in URL_SETTING or state != "unreachable":
                raise ValueError(
                    f"Case {case_id}: services may only make {sorted(URL_SETTING)} 'unreachable'"
                )
    return cases


def sources_needed(cases: list[dict[str, Any]]) -> set[str]:
    """The live services some case expects a successful lookup from."""
    needed = set()
    for case in cases:
        nodes = case["checks"].get("nodes", [])
        unreachable = case.get("services") or {}
        needed |= {
            source
            for source in URL_SETTING
            if NODE_OF_SOURCE[source] in nodes and source not in unreachable
        }
    return needed


def preflight(needed: set[str]) -> list[str]:
    """Why each needed service cannot be used now; empty when all of them answer.

    One read-only lookup each: a ticket or product that does not exist answers
    `not_found`, which proves the service is up and, for the inventory, that
    the account can sign in.
    """
    problems = []
    settings = get_agent_settings()
    if INCIDENT_MANAGER in needed:
        ticket = incidents.lookup_ticket(TicketLookupInput(reference="TRF-999999"))
        if ticket.outcome == "unavailable":
            problems.append(
                f"the incident manager at {settings.incidents_api_url} ({ticket.failure})"
            )
    if INVENTORY_MANAGER in needed:
        stock = inventory.lookup_stock(StockLookupInput(product="recorder preflight"))
        if stock.outcome == "unavailable":
            problems.append(
                f"the inventory manager at {settings.inventory_api_url} ({stock.failure})"
            )
    return problems


@contextmanager
def service_overrides(case: dict[str, Any]) -> Iterator[None]:
    """Point the services the case marks unreachable at a closed port, for this case only."""
    overridden = {URL_SETTING[source]: UNREACHABLE_URL for source in case.get("services") or {}}
    previous = {name: os.environ.get(name) for name in overridden}
    os.environ.update(overridden)
    get_agent_settings.cache_clear()
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_agent_settings.cache_clear()


def record(cases: list[dict[str, Any]], store_path: Path) -> dict[str, Any]:
    """Run every case through a freshly compiled graph; return the traces document.

    Raises AgentRunError on the first failed run.
    """
    graph = build_agent_graph(InMemorySaver())
    store = TraceStore(store_path)
    recorded = []
    for case in cases:
        with service_overrides(case):
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
        "tool_timeout_seconds": get_agent_settings().agent_tool_timeout_seconds,
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
    unreachable = preflight(sources_needed(cases))
    if unreachable:
        print(f"Cannot record: no answer from {'; '.join(unreachable)}", file=sys.stderr)
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
