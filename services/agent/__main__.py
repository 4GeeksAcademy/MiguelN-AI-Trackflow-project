"""Knowledge agent CLI: `npm run agent -- <command>` (`python -m services.agent`).

    check                 compile the graph, print its nodes, version and Mermaid diagram
    ask "<question>"      run the graph once and print the answer and run id
    trace <run_id>|--last print a run's trace: nodes, decisions and outputs in order
    runs [--limit N]      list recent runs
    resume <run_id>       continue a failed run from the node that failed

Runs and traces go to the same store as the API's (`AGENT_STORE_PATH`). `ask`
and `resume` need Qdrant and the models (`RagSettings`); the others do not.
Exit codes: 0 done, 1 the run failed, 2 bad arguments or an unknown run.
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from services.agent import runner
from services.agent.errors import (
    AgentGraphBuildError,
    AgentRunError,
    AgentRunNotFound,
    AgentRunNotResumable,
)
from services.agent.graph import NODES, build_agent_graph, graph_fingerprint
from services.agent.tracing import RunTrace, TraceEvent, get_trace_store

PREVIEW_CHARS = 160


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[: PREVIEW_CHARS - 1] + "…"


def _describe_output(event: TraceEvent) -> str:
    if event.kind == "edge":
        out = event.output or {}
        return f"-> {out.get('to')} ({out.get('decision')}): {out.get('reason')}"
    if event.status == "error":
        return f"ERROR {event.error_type}: {event.error_message or '(message withheld)'}"
    output = event.output or {}
    if "context" in output:
        chunks = output["context"] or []
        hits = ", ".join(
            f"{c.get('source_document')}#{c.get('chunk_index')}={c.get('score', 0):.3f}"
            for c in chunks
        )
        return f"{len(chunks)} chunk(s): {hits or 'none'}"
    return _preview(json.dumps(output, ensure_ascii=False))


def _print_trace(trace: RunTrace) -> None:
    print(
        f"Run {trace.run_id} · {trace.status} · {trace.outcome or '-'} · "
        f"graph {trace.graph_version} · {trace.attempts} attempt(s)"
    )
    print(f"Question: {_preview(trace.question) or '(empty)'}")
    for event in trace.events:
        timing = f"{event.duration_ms:9.1f} ms" if event.duration_ms is not None else " " * 12
        print(
            f"{event.seq:3}  [{event.attempt}] {event.kind:4} {event.name:22} {timing}  "
            f"{_describe_output(event)}"
        )
    if trace.status == "failed":
        print(f"Failed at: {trace.error_node} ({trace.error_type})")
    if trace.answer:
        print(f"Answer: {trace.answer}")


def _check() -> int:
    try:
        compiled = build_agent_graph(InMemorySaver())
    except AgentGraphBuildError as error:
        print(f"The graph does not compile: {error}", file=sys.stderr)
        return 1
    print(f"Graph compiled · version {graph_fingerprint()} · {len(NODES)} nodes")
    for spec in NODES:
        print(f"  {spec.name:22} reads {sorted(spec.reads)} -> writes {sorted(spec.writes)}")
    print()
    print(compiled.get_graph().draw_mermaid())
    return 0


def _report_failure(error: AgentRunError) -> int:
    print(error.public_message, file=sys.stderr)
    print(f"Run id: {error.run_id}", file=sys.stderr)
    if error.retryable:
        print(f"Resume it with: npm run agent -- resume {error.run_id}", file=sys.stderr)
    return 1


def _print_result(result: runner.AgentResult) -> None:
    print(f"Run id:  {result.run_id}")
    print(f"Outcome: {result.outcome}")
    print(result.answer if result.answer is not None else f"Refused: {result.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.agent", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="compile the graph and print it")
    ask = commands.add_parser("ask", help="run the graph on one question")
    ask.add_argument("question")
    trace = commands.add_parser("trace", help="print a run's trace")
    target = trace.add_mutually_exclusive_group(required=True)
    target.add_argument("run_id", nargs="?")
    target.add_argument("--last", action="store_true", help="the most recent run")
    trace.add_argument("--json", action="store_true", help="the whole trace as JSON")
    runs = commands.add_parser("runs", help="list recent runs")
    runs.add_argument("--limit", type=int, default=20)
    resume = commands.add_parser("resume", help="continue a failed run")
    resume.add_argument("run_id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "check":
        return _check()

    if args.command == "ask":
        try:
            _print_result(runner.run_agent(args.question))
        except AgentRunError as error:
            return _report_failure(error)
        return 0

    store = get_trace_store()
    if args.command == "trace":
        run_id = args.run_id
        if args.last:
            latest = store.list_runs(limit=1)
            if not latest:
                print("No runs recorded yet.", file=sys.stderr)
                return 2
            run_id = latest[0].run_id
        found = store.get_trace(run_id)
        if found is None:
            print(f"No run {run_id}", file=sys.stderr)
            return 2
        if args.json:
            data: dict[str, Any] = asdict(found)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            _print_trace(found)
        return 0

    if args.command == "runs":
        for run in store.list_runs(limit=args.limit):
            print(
                f"{run.started_at}  {run.run_id}  {run.status:9} {run.outcome or '-':14} "
                f"{_preview(run.question)[:60]}"
            )
        return 0

    try:
        _print_result(runner.resume_run(args.run_id))
    except AgentRunNotFound:
        print(f"No run {args.run_id}", file=sys.stderr)
        return 2
    except AgentRunNotResumable as error:
        print(str(error), file=sys.stderr)
        return 2
    except AgentRunError as error:
        return _report_failure(error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
