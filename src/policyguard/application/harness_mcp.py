"""Bounded MCP Streamable-HTTP client registry and tool-collision controls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from policyguard.application.product_experience import validate_outbound_url
from policyguard.application.provider_http import ProviderRetryPolicy, post_with_retry


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    url: str
    api_key: str = ""
    timeout_seconds: float = 20
    max_result_bytes: int = 256_000
    allowed_hosts: tuple[str, ...] = ()


class MCPClientRegistry:
    def __init__(self, servers: list[MCPServerConfig]) -> None:
        if len({server.name for server in servers}) != len(servers):
            raise ValueError("mcp_server_name_collision")
        self.servers = {server.name: server for server in servers}
        self.tool_owners: dict[str, str] = {}

    def register_tools(self, server: str, definitions: list[dict[str, Any]]) -> None:
        if server not in self.servers:
            raise LookupError("mcp_server_not_found")
        for definition in definitions:
            name = definition.get("name", "")
            owner = self.tool_owners.get(name)
            if owner and owner != server:
                raise ValueError(f"mcp_tool_collision:{name}:{owner}:{server}")
            self.tool_owners[name] = server

    def health(self, server: str) -> dict[str, Any]:
        return self._request(server, "ping", {})

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        owner = self.tool_owners.get(tool)
        if owner and owner != server:
            raise ValueError("mcp_tool_owner_mismatch")
        return self._request(server, "tools/call", {"name": tool, "arguments": arguments})

    def _request(self, server_name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        server = self.servers.get(server_name)
        if server is None:
            raise LookupError("mcp_server_not_found")
        validate_outbound_url(server.url, set(server.allowed_hosts) or None)
        response = post_with_retry(
            server.url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **({"Authorization": f"Bearer {server.api_key}"} if server.api_key else {}),
            },
            json={"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params},
            timeout=server.timeout_seconds,
            policy=ProviderRetryPolicy(max_attempts=3),
        )
        content = response.content
        if len(content) > server.max_result_bytes:
            raise ValueError("mcp_result_too_large")
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("mcp_response_invalid") from exc
        if payload.get("error"):
            raise RuntimeError(f"mcp_remote_error:{payload['error'].get('code', 'unknown')}")
        return {
            "server": server_name,
            "method": method,
            "result": payload.get("result", {}),
            "provider_attempts": response.extensions.get("policyguard_attempts", 1),
        }


def response_with_json(payload: dict[str, Any]) -> httpx.Response:
    """Small test helper kept here to document the expected MCP response envelope."""
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": "test", "result": payload})
