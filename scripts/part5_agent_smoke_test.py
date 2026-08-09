from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.client import SensorMCPClient
from mcp_server.repository import SensorRepository
from mcp_server.server import DEFAULT_MODEL_RUN_ID, database_url_from_environment
from rag.agent_workflow import ToolCallingSmartFactoryAgent
from web_app.app import create_app


def main() -> None:
    repository = SensorRepository(database_url_from_environment(), DEFAULT_MODEL_RUN_ID)
    agent = ToolCallingSmartFactoryAgent(SensorMCPClient())
    cases = {
        "센서 92 상태 알려줘": {"get_sensor_status"},
        "진동이 상승하면 무엇을 점검해야 해?": {"search_maintenance_knowledge"},
        "센서 92 상태와 점검 방법을 알려줘": {
            "get_sensor_status",
            "search_maintenance_knowledge",
        },
    }
    for question, expected_tools in cases.items():
        result = agent.ask(question)
        called = {item["tool"] for item in result["tool_trace"]}
        if not expected_tools.issubset(called):
            raise RuntimeError(f"Missing Tool call: expected={expected_tools}, result={result}")
        if result["generation_mode"] != "deterministic_offline_fallback":
            raise RuntimeError(result)
        print(question, "→", " → ".join(item["tool"] for item in result["tool_trace"]))

    app = create_app(repository=repository, rag_agent=agent)
    app.testing = True
    client = app.test_client()
    response = client.post(
        "/api/ask", json={"question": "센서 92 상태와 점검 방법을 알려줘"}
    )
    if response.status_code != 200 or response.get_json()["route"] != "agent":
        raise RuntimeError(response.get_json())
    invalid = client.post("/api/ask", json={"question": ""})
    if invalid.status_code != 400:
        raise RuntimeError(f"Expected HTTP 400, got {invalid.status_code}")
    print("Part 5 offline and Flask smoke tests passed")


if __name__ == "__main__":
    main()
