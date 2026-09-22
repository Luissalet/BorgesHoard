"""Boot the real app in a subprocess, then talk to it over HTTP and through the MCP stdio bridge."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from borges.port import free_port

ROOT = Path(__file__).resolve().parent.parent


def wait_health(url: str, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"app exited early with code {process.returncode}")
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("app did not become healthy")


@pytest.fixture
def app_process(tmp_path):
    port = free_port()
    data_dir = tmp_path / "data"
    env = {
        **os.environ,
        "BORGES_DATA_DIR": str(data_dir),
        "BORGES_PORT": str(port),
        "PORT_STRICT": "1",
        "BORGES_EMBED": "fake",
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen([sys.executable, "-m", "borges"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_health(url, process)
        yield url, data_dir, env
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_subprocess_http_and_mcp_bridge(app_process, library):
    url, data_dir, env = app_process
    health = httpx.get(f"{url}/api/health").json()
    assert health["service"] == "borges-hoard" and health["dataDirConfigured"] is True
    tools = httpx.get(f"{url}/api/agent/tools").json()["tools"]
    assert [t["name"] for t in tools][:2] == ["library_search", "library_read"]

    token = (data_dir / "mcp-token").read_text().strip()
    assert len(token) == 64
    assert httpx.post(f"{url}/api/agent/call", json={"name": "library_status"}).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}
    added = httpx.post(f"{url}/api/agent/call", json={"name": "library_add_collection", "arguments": {"path": str(library), "name": "E2E"}}, headers=auth).json()
    assert added["ok"] is True

    deadline = time.time() + 60
    while time.time() < deadline:
        status = httpx.get(f"{url}/api/status").json()
        if status["counts"]["documents"] >= 8 and not status["worker"]["busy"]:
            break
        time.sleep(0.3)
    assert status["counts"]["documents"] == 8 and status["model"]["backend"] == "fake"

    async def through_mcp():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp_server.py")],
            env={**env, "BORGES_URL": url, "BORGES_TOKEN_FILE": str(data_dir / "mcp-token")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert "library_search" in (init.instructions or "")
                listed = await session.list_tools()
                names = [t.name for t in listed.tools]
                assert names == [t["name"] for t in tools]
                write = next(t for t in listed.tools if t.name == "library_add_collection")
                assert write.annotations.readOnlyHint is False and write.annotations.destructiveHint is False
                result = await session.call_tool("library_search", {"q": "vertedero de basuras", "limit": 3})
                payload = json.loads(result.content[0].text)
                assert payload["count"] == 3 and "«Cuentos Valdeniebla», p. 1" in {h["citation"] for h in payload["hits"]}
                hit = payload["hits"][0]
                read_result = json.loads((await session.call_tool("library_read", {"document_id": hit["document_id"], "chunk_id": hit["chunk_id"]})).content[0].text)
                assert "vertedero" in read_result["text"]
                bad = json.loads((await session.call_tool("library_document", {"document_id": 999999})).content[0].text)
                assert "error" in bad

    asyncio.run(through_mcp())
