"""Knowledge agent CLI: `npm run agent -- <command>` (`python -m services.agent`).

    check                 compile the graph, print its nodes, version and Mermaid diagram
    ask "<question>" [--conversation ID] [--user NAME]
                          run the graph once and print the answer, run and conversation
    chat [--conversation ID] [--user NAME]
                          a multi-turn conversation in the terminal (empty line or Ctrl-D
                          to leave); answer "Want me to remember this?" here
    trace <run_id>|--last print a run's trace: nodes, decisions and outputs in order
    runs [--limit N]      list recent runs
    resume <run_id>       continue a failed run from the node that failed
    memory list [--all]   the team memory: active entries (--all: ended ones too)
    memory log [--conversation ID] [--limit N]
                          the memory decision log (append-only), oldest first
    memory show <memory_id>
                          one memory, and every decision about it
    memory cleanup        expire what is past its date and apply the caps (logged)

`--user` is who approves a memory in a CLI conversation (default `cli:<os user>`):
every memory records who authorized it.

Runs and traces go to the same store as the API's (`AGENT_STORE_PATH`). `ask`
and `resume` need the models (`RagSettings`), plus Qdrant, and for questions
about tickets or stock the TrackFlow MCP server (`AGENT_MCP_URL`) with the
agent's OAuth client (`AGENT_OAUTH_*`); the others need none of them.
Exit codes: 0 done, 1 the run failed, 2 bad arguments or an unknown run.
"""

import argparse
import getpass
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
from services.agent.memory.store import ConversationNotFound, Memory, get_memory_store
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
    if "memory_reply" in output:
        reply = output["memory_reply"] or {}
        decision = reply.get("decision") or {}
        rest = reply.get("remaining_question") or ""
        return f"reply -> {decision.get('outcome')}: {decision.get('reason')}" + (
            f"; continues with {_preview(rest)!r}" if rest else ""
        )
    if "memory_resolution" in output:
        resolution = output["memory_resolution"] or {}
        return f"proposal {resolution.get('outcome')}" + (
            f" -> memory {resolution['memory_id']}" if resolution.get("memory_id") else ""
        )
    if "memory_recall" in output:
        recall = output["memory_recall"] or {}
        items = recall.get("items") or []
        found = ", ".join(f"{i.get('scope')} ({i.get('matched_by')})" for i in items)
        return f"memory {recall.get('outcome')}: {found or 'nothing relevant'}"
    if "memory_proposal" in output:
        proposal = output["memory_proposal"] or {}
        return f"proposal {proposal.get('status')}: {_preview(proposal.get('reason') or '')}"
    if "memory_candidate" in output:
        candidate = output["memory_candidate"] or {}
        return (
            f"answer + self-evaluation {candidate.get('memory_check')}: "
            f"{_preview(candidate.get('reason') or '')}"
        )
    if "plan" in output:
        plan = output["plan"] or {}
        steps = " then ".join(step.get("source", "?") for step in plan.get("steps") or [])
        ignored = f"; ignored: {'; '.join(plan['ignored'])}" if plan.get("ignored") else ""
        return f"plan ({plan.get('decided_by')}): {steps}{ignored}"
    for field in ("ticket_lookup", "stock_lookup"):
        if field in output:
            return _describe_lookup(output[field] or {})
    if "context" in output:
        chunks = output["context"] or []
        hits = ", ".join(
            f"{c.get('source_document')}#{c.get('chunk_index')}={c.get('score', 0):.3f}"
            for c in chunks
        )
        return f"{len(chunks)} chunk(s): {hits or 'none'}"
    return _preview(json.dumps(output, ensure_ascii=False))


def _describe_lookup(result: dict[str, Any]) -> str:
    """'MCP get_incident(reference=TRF-000012) -> ok (1 found, 12.0 ms)'."""
    outcome = result.get("outcome")
    if outcome == "ok":
        detail = f"{result.get('total', 0)} found"
    else:
        detail = str(result.get("failure") or outcome)
    return (
        f"{result.get('endpoint')} -> {outcome} ({detail}, {result.get('duration_ms', 0):.1f} ms)"
    )


def _print_trace(trace: RunTrace) -> None:
    print(
        f"Run {trace.run_id} · {trace.status} · {trace.outcome or '-'} · "
        f"graph {trace.graph_version} · {trace.attempts} attempt(s)"
    )
    if trace.conversation_id:
        print(f"Conversation: {trace.conversation_id}")
    print(f"Question: {_preview(trace.question) or '(empty)'}")
    print(f"Sources: {' -> '.join(trace.sources()) or 'none'}")
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
    print(f"Conversation: {result.conversation_id}")
    print(f"Outcome: {result.outcome}")
    print(result.answer if result.answer is not None else f"Refused: {result.error}")


def _default_user() -> str:
    return f"cli:{getpass.getuser()}"


def _chat(conversation_id: str | None, user: str) -> int:
    print(f"Conversation as {user}. An empty line or Ctrl-D leaves.")
    while True:
        try:
            message = input("you> ")
        except EOFError:
            print()
            break
        if not message.strip():
            break
        try:
            result = runner.run_agent(message, user_id=user, conversation_id=conversation_id)
        except AgentRunError as error:
            _report_failure(error)
            continue
        conversation_id = result.conversation_id
        print(f"agent> {result.answer if result.answer is not None else result.error}")
        print(f"       (run {result.run_id} · {result.outcome})")
    if conversation_id:
        print(f"Conversation id: {conversation_id}")
    return 0


def _print_memory(memory: Memory) -> None:
    print(
        f"{memory.memory_id}  {memory.status:10} {memory.category:24} [{memory.scope()}]\n"
        f"    {memory.content}\n"
        f"    approved by {memory.approved_by} on {memory.approved_at}; valid until "
        f"{memory.valid_until}; recalled {memory.recall_count}x"
        + (f"; ended {memory.ended_at}" if memory.ended_at else "")
    )


def _memory_command(args: argparse.Namespace) -> int:
    store = get_memory_store()
    if args.memory_command == "list":
        memories = store.all_memories() if args.all else store.active_memories()
        if not memories:
            print("The team memory is empty.")
        for memory in memories:
            _print_memory(memory)
        return 0
    if args.memory_command == "log":
        for decision in store.decision_log(conversation_id=args.conversation, limit=args.limit):
            subject = decision.memory_id or decision.proposal_id or "-"
            label = (
                f" [{decision.label} {decision.confidence:.2f}]"
                if decision.label and decision.confidence is not None
                else ""
            )
            print(
                f"{decision.decided_at}  #{decision.decision_id:<4} {decision.event:10} "
                f"by {decision.user_id or '-'}{label} · {subject}\n"
                f"    why: {decision.reason}"
                + (f"\n    message: {_preview(decision.message)}" if decision.message else "")
                + (f"\n    content: {_preview(decision.content)}" if decision.content else "")
            )
        return 0
    if args.memory_command == "show":
        shown = store.get_memory(args.memory_id)
        if shown is None:
            print(f"No memory {args.memory_id}", file=sys.stderr)
            return 2
        _print_memory(shown)
        for decision in store.decision_log(limit=100_000):
            if decision.memory_id == shown.memory_id or (
                decision.proposal_id == shown.source_proposal_id
            ):
                print(f"  {decision.decided_at}  {decision.event:10} {decision.reason}")
        return 0
    report = store.cleanup()
    print(
        f"Expired memories: {len(report.expired_memories)} · expired proposals: "
        f"{len(report.expired_proposals)} · evicted: {len(report.evicted_memories)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.agent", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="compile the graph and print it")
    ask = commands.add_parser("ask", help="run the graph on one question")
    ask.add_argument("question")
    ask.add_argument("--conversation", help="continue this conversation")
    ask.add_argument("--user", default=_default_user(), help="who is asking (and approving)")
    chat = commands.add_parser("chat", help="a multi-turn conversation in the terminal")
    chat.add_argument("--conversation", help="continue this conversation")
    chat.add_argument("--user", default=_default_user(), help="who is asking (and approving)")
    memory = commands.add_parser("memory", help="inspect the team memory and its log")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_list = memory_commands.add_parser("list", help="the active memories")
    memory_list.add_argument("--all", action="store_true", help="ended memories too")
    memory_log = memory_commands.add_parser("log", help="the decision log")
    memory_log.add_argument("--conversation")
    memory_log.add_argument("--limit", type=int, default=100)
    memory_show = memory_commands.add_parser("show", help="one memory and its decisions")
    memory_show.add_argument("memory_id")
    memory_commands.add_parser("cleanup", help="expire and apply the caps")
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

    if args.command in {"ask", "chat"}:
        try:
            if args.command == "chat":
                return _chat(args.conversation, args.user)
            _print_result(
                runner.run_agent(
                    args.question, user_id=args.user, conversation_id=args.conversation
                )
            )
        except ConversationNotFound:
            print(f"No conversation {args.conversation} for {args.user}", file=sys.stderr)
            return 2
        except AgentRunError as error:
            return _report_failure(error)
        return 0

    if args.command == "memory":
        return _memory_command(args)

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
