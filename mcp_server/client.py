from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_ENV_NAMES = {
    "DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "MCP_MODEL_RUN_ID",
}
class MCPToolError(RuntimeError):
    pass


def _run_async(coroutine) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    # Flask is synchronous today, but keep the adapter safe if called by an
    # async host later. A separate thread owns the temporary MCP event loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()


class SensorMCPClient:
    """Synchronous adapter for the project's read-only MCP stdio server."""

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root

    def _server_parameters(self) -> StdioServerParameters:
        file_values = dotenv_values(self.project_root / ".env")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GOOGLE_API_KEY", "TAVILY_API_KEY"}
        }
        for name in SERVER_ENV_NAMES:
            value = file_values.get(name)
            if value:
                environment[name] = value
        return StdioServerParameters(
            command=str(self.project_root / ".venv/bin/python"),
            args=["-m", "mcp_server.server"],
            cwd=self.project_root,
            env=environment,
        )

    async def _call_tools(
        self, calls: Sequence[tuple[str, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        parameters = self._server_parameters()
        responses: dict[str, dict[str, Any]] = {}
        failure: MCPToolError | None = None
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    if result.is_error:
                        failure = MCPToolError(f"MCP tool failed: {name}")
                        break
                    payload = result.structured_content
                    if not isinstance(payload, dict):
                        failure = MCPToolError(
                            f"MCP tool returned no structured content: {name}"
                        )
                        break
                    responses[name] = payload
        if failure is not None:
            raise failure
        return responses

    async def _inspect_server(self) -> dict[str, Any]:
        parameters = self._server_parameters()
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                tools = [
                    {
                        "name": item.name,
                        "description": item.description or "",
                    }
                    for item in result.tools
                ]
        return {
            "available": True,
            "transport": "stdio",
            "server": "smart-factory-scada",
            "mode": "read-only",
            "database": "TimescaleDB / PostgreSQL",
            "tool_count": len(tools),
            "tools": tools,
        }

    def call_tools(
        self, calls: Sequence[tuple[str, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        return _run_async(self._call_tools(calls))

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call_tools([(name, arguments or {})])[name]

    def inspect_server(self) -> dict[str, Any]:
        return _run_async(self._inspect_server())

    def get_dashboard_overview(self) -> dict[str, Any]:
        responses = self.call_tools(
            [("list_monitored_sensors", {}), ("get_factory_summary", {})]
        )
        return {
            "factory_summary": responses["get_factory_summary"],
            "sensors": responses["list_monitored_sensors"]["sensors"],
        }

    def get_sensor_status(self, sensor_id: str) -> dict[str, Any]:
        return self.call_tool("get_sensor_status", {"sensor_id": sensor_id})

    def get_factory_overview(self) -> dict[str, Any]:
        responses = self.call_tools(
            [
                ("get_factory_summary", {}),
                ("get_abnormal_sensors", {"minimum_status": "DEGRADING", "limit": 20}),
            ]
        )
        return {
            "factory_summary": responses["get_factory_summary"],
            "abnormal_sensors": responses["get_abnormal_sensors"],
        }
