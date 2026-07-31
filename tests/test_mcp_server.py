import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import policyguard.mcp_server as mcp_server

PROJECT_ROOT = Path(__file__).parents[1]


def test_mcp_exposes_only_declared_tools() -> None:
    names = [tool.name for tool in mcp_server.mcp._tool_manager.list_tools()]

    assert names == [
        "search_policy",
        "get_workflow",
        "create_remediation_plan",
        "create_internal_draft",
    ]


def test_mcp_search_is_read_only_evidence() -> None:
    result = mcp_server.search_policy("国家级广告", market="CN", top_k=2)

    assert result["evidence_only"] is True
    assert result["retriever"] in {
        "lexical_bm25_cjk_v1",
        "lexical_bm25_cjk_v1_fallback",
        "hybrid_rrf",
        "hybrid_rrf_fallback",
    }
    assert result["results"][0]["section_id"] == "article-9"


def test_mcp_search_rejects_invalid_top_k() -> None:
    with pytest.raises(ValueError, match="top_k_out_of_range"):
        mcp_server.search_policy("国家级广告", top_k=0)


def test_real_mcp_stdio_handshake_and_tool_call() -> None:
    async def run() -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "policyguard.mcp_server"],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 4
                result = await session.call_tool(
                    "search_policy", {"query": "国家级广告", "market": "CN", "top_k": 2}
                )
                assert result.isError is False
                assert "article-9" in str(result.content)

    asyncio.run(run())
