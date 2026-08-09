from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    env = os.environ.copy()
    env.update({key: value for key, value in dotenv_values(PROJECT_ROOT / ".env").items() if value})
    parameters = StdioServerParameters(
        command=str(PROJECT_ROOT / ".venv/bin/python"),
        args=["-m", "mcp_server.server"],
        cwd=PROJECT_ROOT,
        env=env,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            expected = {
                "list_monitored_sensors",
                "get_model_summary",
                "get_sensor_status",
                "get_abnormal_sensors",
                "get_sensor_history",
                "get_anomaly_detail",
                "get_factory_summary",
            }
            missing = sorted(expected - set(names))
            if missing:
                raise RuntimeError(f"Missing MCP tools: {missing}")
            result = await session.call_tool("get_sensor_status", {"sensor_id": "84"})
            if result.is_error:
                raise RuntimeError(result.content)
            payload = result.structured_content
            if payload is None or payload.get("sensor_id") != "84":
                raise RuntimeError(f"Unexpected structured response: {payload}")
            print(json.dumps({"tools": names, "sensor_84": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
