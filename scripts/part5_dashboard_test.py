from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.client import MCPToolError
from web_app.app import create_app


class FakeDashboardClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _check(self):
        if self.fail:
            raise MCPToolError("secret connection detail")

    def get_dashboard_overview(self) -> dict[str, Any]:
        self._check()
        return {
            "factory_summary": {
                "as_of": "2019-07-30 20:42:00",
                "model_run_id": "mock-run",
                "monitored_sensor_count": 3,
                "normal_count": 1,
                "attention_count": 0,
                "degrading_count": 2,
                "warning_count": 0,
            },
            "sensors": [
                {
                    "sensor_id": sensor_id,
                    "unit": "mg",
                    "latest_window": "2019-07-30 20:42:00",
                    "latest_status": "DEGRADING" if sensor_id == "92" else "NORMAL",
                    "latest_risk_score": 81.83 if sensor_id == "92" else 20.1,
                }
                for sensor_id in ["84", "92", "109"]
            ],
        }

    def inspect_server(self) -> dict[str, Any]:
        self._check()
        tools = [{"name": f"tool-{index}", "description": "read-only"} for index in range(7)]
        return {
            "available": True,
            "transport": "stdio",
            "server": "smart-factory-scada",
            "mode": "read-only",
            "database": "TimescaleDB / PostgreSQL",
            "tool_count": 7,
            "tools": tools,
        }

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._check()
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "get_sensor_status":
            return {
                "sensor_id": arguments["sensor_id"],
                "as_of": "2019-07-30 20:42:00",
                "status": "DEGRADING",
                "risk_score": 81.83,
                "unit": "mg",
                "sample_count": 90,
                "gap_minutes": 1.0,
                "rms": 445.509,
                "peak_to_peak": 2059.074,
                "sigma_detected_features": ["rms"],
            }
        if name == "get_sensor_history":
            return {
                "sensor_id": arguments["sensor_id"],
                "as_of": "2019-07-30 20:42:00",
                "returned_count": 2,
                "windows": [
                    {
                        "window_start": "2019-07-30 20:42:00",
                        "risk_score": 81.83,
                        "rms": 445.509,
                        "peak_to_peak": 2059.074,
                    },
                    {
                        "window_start": "2019-07-30 20:41:00",
                        "risk_score": 70.0,
                        "rms": 400.0,
                        "peak_to_peak": 1800.0,
                    },
                ],
            }
        if name == "get_model_summary":
            return {"model_run_id": "mock-run", "result_count": 10}
        if name == "list_monitored_sensors":
            return {"sensor_count": 3, "sensors": []}
        if name == "get_factory_summary":
            return self.get_dashboard_overview()["factory_summary"]
        if name == "get_abnormal_sensors":
            return {"count": 1, "sensors": [{"sensor_id": "92"}]}
        if name == "get_anomaly_detail":
            return {"sensor_id": arguments["sensor_id"], **arguments}
        raise RuntimeError(f"unexpected Tool: {name}")


class FakeAgent:
    def ask(self, question: str) -> dict[str, Any]:
        if not question.strip():
            raise ValueError("question is required")
        return {
            "question": question,
            "route": "agent",
            "sensor_id": "92",
            "answer": "2019년 마지막 저장 시각 기준 답변입니다.",
            "agent_mode": "tool_calling",
            "agent_step_count": 2,
            "generation_mode": "google_gemini_tool_agent",
            "generation_error": None,
            "tool_trace": [
                {
                    "step": 1,
                    "tool": "get_sensor_status",
                    "type": "mcp",
                    "arguments": {"sensor_id": "92"},
                    "status": "success",
                    "summary": "DEGRADING, Risk Score 81.83",
                }
            ],
            "citations": [
                {
                    "type": "local_document",
                    "source": "vibration_triage.md",
                    "title": "진동 상승 시 확인 순서",
                    "chunk": 2,
                    "excerpt": "센서 체결 상태를 확인한다.",
                }
            ],
            "sensor_data": {},
            "sensor_data_source": "mcp",
            "retrieved_document_count": 1,
            "web_search_used": False,
            "web_result_count": 0,
            "web_search_error": None,
            "router_mode": "not_used",
            "router_reason": "Tool 선택",
            "router_error": None,
        }


def main() -> None:
    sensor_client = FakeDashboardClient()
    app = create_app(
        repository=object(), rag_agent=FakeAgent(), sensor_client=sensor_client
    )
    app.testing = True
    client = app.test_client()

    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for marker in [
        "Smart Factory",
        "Historical Dataset · 2019",
        "AGENT EXECUTION",
        "SOURCES / EVIDENCE",
        "MCP Runtime",
        "history-chart",
    ]:
        assert marker in html, marker

    assert client.get("/static/dashboard.css").status_code == 200
    dashboard_js = client.get("/static/js/dashboard.js")
    assert dashboard_js.status_code == 200
    script = dashboard_js.get_data(as_text=True)
    for marker in ["selectSensor", "getSensorHistory", "renderTrace", "renderSources"]:
        assert marker in script, marker

    overview = client.get("/api/dashboard")
    assert overview.status_code == 200
    assert len(overview.get_json()["sensors"]) == 3

    status = client.get("/api/sensors/92/status")
    assert status.status_code == 200
    assert status.get_json()["status"] == "DEGRADING"
    history = client.get("/api/sensors/92/history?hours=24&limit=200")
    assert history.status_code == 200
    assert history.get_json()["returned_count"] == 2
    assert ("get_sensor_history", {"sensor_id": "92", "hours": 24, "limit": 200}) in sensor_client.calls

    mcp = client.get("/api/system/mcp")
    assert mcp.status_code == 200
    assert mcp.get_json()["available"] is True
    assert mcp.get_json()["tool_count"] == 7

    answer = client.post("/api/ask", json={"question": "센서 92 상태 알려줘"})
    assert answer.status_code == 200
    payload = answer.get_json()
    assert payload["tool_trace"][0]["type"] == "mcp"
    assert payload["citations"][0]["excerpt"]

    assert client.get("/api/sensors/not-a-number/status").status_code == 400
    assert client.get("/api/sensors/92/history?hours=0").status_code == 400
    assert client.post("/api/ask", json={"question": ""}).status_code == 400

    failing_app = create_app(
        repository=object(), rag_agent=FakeAgent(), sensor_client=FakeDashboardClient(fail=True)
    )
    failing_app.testing = True
    failing_app.logger.disabled = True
    failed = failing_app.test_client().get("/api/dashboard")
    assert failed.status_code == 503
    body = failed.get_data(as_text=True)
    assert "secret connection detail" not in body
    assert "센서 데이터를 조회하지 못했습니다" in failed.get_json()["message"]

    print("Part 5 Smart Factory dashboard tests passed")


if __name__ == "__main__":
    main()
