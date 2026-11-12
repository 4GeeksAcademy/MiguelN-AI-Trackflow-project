"""Knowledge agent (Milestone 7, Part 1): the RAG flow as an explicit LangGraph graph.

The same behaviour as `data/pipelines/rag.py::query()`, made inspectable: one
node per step, a minimal state, conditional transitions, a checkpoint after
every step and a trace for every run. Design notes: `docs/agent/agent-graph.md`.

- `state.py`   the state the nodes share, and the fixed answers;
- `nodes.py`   one function per step, reusing `rag.retrieve` / `rag.generate_answer`;
- `graph.py`   node contracts, edges, routing, compilation and its structural checks;
- `tracing.py` the queryable run trace (SQLite);
- `runner.py`  runs or resumes the graph and writes the trace;
- `router.py`  `POST /agent/query` and `GET /agent/runs/{run_id}`, HTTP only.
"""
