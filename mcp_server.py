"""Stdio MCP bridge for Borges's Hoard.

It never opens the database: every tool call is proxied to the running app (`POST /api/agent/call`) with the Bearer token
from `<DATA_DIR>/mcp-token`. The tool list comes from `GET /api/agent/tools` (refreshed while the bridge runs), so the
bridge and the app can never disagree. When nothing answers, the bridge starts the app itself (`python -m borges`, detached,
on the port of BORGES_URL) and waits for it; BORGES_BRIDGE_AUTOSTART=0 turns that off. The bridge is the shared
`hoard_link.bridge.CatalogBridge`.
"""

from __future__ import annotations

import sys

from borges.hoard_link.bridge import CatalogBridge


def build() -> CatalogBridge:
    return CatalogBridge(app="borges", service="borges-hoard", package="borges", default_port=5184, data_dir_env="BORGES_DATA_DIR",
                         title="Borges's Hoard", root=__file__)


if __name__ == "__main__":
    sys.exit(build().run_bridge())
