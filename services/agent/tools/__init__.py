"""The agent's tools: live, read-only lookups in the services TrackFlow already runs.

- `contracts`: the typed input and output of each tool.
- `incidents`: ticket lookup in the incident manager (`GET /api/incidents`).
- `inventory`: stock lookup in the inventory manager (`GET /inventory/products`).
- `auth`: the stock tool's sign-in with the configured service account.
- `http`: the single GET helper with the numeric timeout and failure labels.
- `passages`: successful lookups rendered as context passages for generation.

Each tool does one thing, is a node of its own in the graph, and turns every
service failure into an `unavailable` result instead of raising.
"""
