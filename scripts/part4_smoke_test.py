from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.repository import SensorRepository
from mcp_server.client import SensorMCPClient
from mcp_server.server import DEFAULT_MODEL_RUN_ID, database_url_from_environment
from rag.workflow import SmartFactoryAgent
from web_app.app import create_app


def main() -> None:
    repository = SensorRepository(database_url_from_environment(), DEFAULT_MODEL_RUN_ID)
    agent = SmartFactoryAgent(SensorMCPClient())
    cases = {
        "센서 92 상태 알려줘": "sensor",
        "진동이 상승하면 무엇을 점검해야 해?": "knowledge",
        "센서 92 상태와 점검 방법을 알려줘": "hybrid",
    }
    for question, expected_route in cases.items():
        result = agent.ask(question)
        if result["route"] != expected_route:
            raise RuntimeError(f"Expected {expected_route}, got {result}")
        if expected_route != "sensor" and result["retrieved_document_count"] < 1:
            raise RuntimeError(f"No RAG documents returned: {result}")
        print(expected_route, result["answer"][:180].replace("\n", " "))

    app = create_app(repository=repository, rag_agent=agent)
    app.testing = True
    client = app.test_client()
    response = client.post("/api/ask", json={"question": "센서 92 상태와 점검 방법을 알려줘"})
    if response.status_code != 200:
        raise RuntimeError(response.data)
    payload = response.get_json()
    if payload["route"] != "hybrid" or payload["sensor_id"] != "92":
        raise RuntimeError(payload)
    invalid = client.post("/api/ask", json={"question": ""})
    if invalid.status_code != 400:
        raise RuntimeError(f"Expected 400, got {invalid.status_code}")
    print("Part 4 smoke test passed")


if __name__ == "__main__":
    main()
