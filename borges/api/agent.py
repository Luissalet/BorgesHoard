"""/api/agent/* — the bridge used by mcp_server.py (Bearer token from <DATA_DIR>/mcp-token). The routes, the token check,
the error envelope and the audit event are the shared agent kit; Borges only supplies its tools."""

from __future__ import annotations

from ..agent_tools import AGENT_INSTRUCTIONS, call_tool, tool_catalog
from ..hoard_link.agentkit import make_agent_router
from .deps import services

router = make_agent_router(
    tools_fn=tool_catalog,
    call_fn=lambda name, arguments, request: call_tool(services(request), name, arguments),
    token_fn=lambda request: services(request).token,
    instructions=AGENT_INSTRUCTIONS,
    app_name="borges",
)
