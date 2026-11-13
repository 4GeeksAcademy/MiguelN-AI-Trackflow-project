"""The knowledge agent's memory (Milestone 8, Part 1, ticket MEM-092).

The agent learns from interaction only through the user: it proposes, the user
decides, and only what was approved - with who approved it and when - is kept.
Design notes: `docs/agent/agent-memory.md`.

- `policy.py`     what may and may never be remembered (`memory-briefing.md`), the
                  consolidation keys and lifetimes, and the deterministic validator;
- `store.py`      the SQLite store and its explicit read/write interface: proposals,
                  the append-only decision log, approved memories, cleanup;
- `classifier.py` the explicit classification of the user's reply to a pending
                  proposal (approve, reject, edit, or neither).

This package never touches the knowledge base: memory is not written to Qdrant,
and the RAG collection `trackflow_knowledge` stays read-only.
"""
