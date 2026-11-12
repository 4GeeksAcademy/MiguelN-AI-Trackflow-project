"""Knowledge agent (Milestone 7): the RAG flow as an explicit LangGraph graph,
plus live tools for the data the knowledge base does not hold.

Part 1 made `data/pipelines/rag.py::query()` inspectable: one node per step, a
minimal state, conditional transitions, a checkpoint after every step and a
trace for every run. Part 2 adds a planner that decides, per question, whether
it needs the knowledge base, a live lookup in the incident or inventory
manager, or both. Design notes: `docs/agent/agent-graph.md`.

- `state.py`   the state the nodes share, the sources, and the fixed answers;
- `planner.py` the model's choice of sources, validated before anything runs;
- `tools/`     the read-only ticket and stock lookups, with their contracts;
- `nodes.py`   one function per step, reusing `rag.retrieve` / `rag.generate_answer`;
- `graph.py`   node contracts, edges, routing, compilation and its structural checks;
- `tracing.py` the queryable run trace (SQLite);
- `runner.py`  runs or resumes the graph and writes the trace;
- `router.py`  `POST /agent/query` and `GET /agent/runs/{run_id}`, HTTP only.
"""
