"""The agent's live lookups, all through the TrackFlow MCP server.

- `contracts`: the typed input (the planner's tool schemas) and output of each lookup.
- `mcp_lookups`: ticket and stock lookups, mapped onto the MCP server's read tools.
- `mcp_client`: the MCP client - tools loaded with `langchain-mcp-adapters` over
  Streamable HTTP, an allow-list of read tools, failures turned into labels.
- `oauth`: the agent's OAuth client-credentials token (read scopes only).
- `passages`: successful lookups rendered as context passages for generation.

The agent has no direct path to the Incidents Manager or the inventory: the
MCP server (`mcps/trackflow_tools`) owns those calls. Each lookup is a node of
its own in the graph and turns every failure into an `unavailable` result
instead of raising.
"""
