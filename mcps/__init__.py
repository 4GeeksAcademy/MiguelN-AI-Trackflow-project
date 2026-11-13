"""The monorepo's MCP servers.

Each subpackage is an independent service that exposes company capabilities
through the Model Context Protocol. A server here reaches the backend
(`services/`) over HTTP, the same way any other client does, and never imports
it: the services own the data and the rules, the MCP server only exposes them.
"""
