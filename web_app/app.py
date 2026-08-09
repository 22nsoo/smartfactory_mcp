from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from flask import Flask, jsonify, render_template, request

from mcp_server.client import MCPToolError, SensorMCPClient
from mcp_server.repository import SensorRepository


SENSOR_ID_PATTERN = re.compile(r"^[0-9]{1,10}$")


def integer_query(name: str, default: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def validated_sensor_id(sensor_id: str) -> str:
    normalized = sensor_id.strip()
    if not SENSOR_ID_PATTERN.fullmatch(normalized):
        raise ValueError("sensor_id must contain 1 to 10 digits")
    return normalized


def create_app(
    repository: SensorRepository | None = None,
    rag_agent=None,
    sensor_client: SensorMCPClient | None = None,
) -> Flask:
    app = Flask(__name__)
    # repository remains in the signature for existing callers; dashboard data
    # access is routed through MCP in Part 5.
    _ = repository
    mcp_client = sensor_client or SensorMCPClient()
    agent_holder = {"value": rag_agent}

    def resolve_agent():
        if agent_holder["value"] is None:
            from rag.agent_workflow import build_tool_calling_agent

            agent_holder["value"] = build_tool_calling_agent()
        return agent_holder["value"]

    @app.errorhandler(ValueError)
    def invalid_request(error: ValueError):
        return jsonify({"error": "invalid_request", "message": str(error)}), 400

    @app.errorhandler(MCPToolError)
    def mcp_error(error: MCPToolError):
        app.logger.exception("MCP Tool request failed")
        return (
            jsonify(
                {
                    "error": "mcp_unavailable",
                    "message": "센서 데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                }
            ),
            503,
        )

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/health")
    def health():
        summary = mcp_client.call_tool("get_model_summary")
        return jsonify(
            {
                "status": "ok",
                "database": "connected",
                "model_run_id": summary["model_run_id"],
                "result_count": summary["result_count"],
            }
        )

    @app.get("/api/model")
    def model_summary():
        return jsonify(mcp_client.call_tool("get_model_summary"))

    @app.get("/api/sensors")
    def monitored_sensors():
        return jsonify(mcp_client.call_tool("list_monitored_sensors"))

    @app.get("/api/sensors/<sensor_id>/status")
    def sensor_status(sensor_id: str):
        sensor_id = validated_sensor_id(sensor_id)
        return jsonify(mcp_client.call_tool("get_sensor_status", {"sensor_id": sensor_id}))

    @app.get("/api/sensors/<sensor_id>/history")
    def sensor_history(sensor_id: str):
        sensor_id = validated_sensor_id(sensor_id)
        hours = integer_query("hours", 24)
        limit = integer_query("limit", 200)
        if not 1 <= hours <= 744:
            raise ValueError("hours must be between 1 and 744")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return jsonify(
            mcp_client.call_tool(
                "get_sensor_history",
                {"sensor_id": sensor_id, "hours": hours, "limit": limit},
            )
        )

    @app.get("/api/abnormal-sensors")
    def abnormal_sensors():
        limit = integer_query("limit", 20)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return jsonify(
            mcp_client.call_tool(
                "get_abnormal_sensors",
                {
                    "minimum_status": request.args.get("minimum_status", "DEGRADING"),
                    "limit": limit,
                },
            )
        )

    @app.get("/api/anomaly-detail")
    def anomaly_detail():
        sensor_id = validated_sensor_id(request.args.get("sensor_id", ""))
        window_start = request.args.get("window_start", "")
        if not window_start:
            raise ValueError("window_start is required")
        try:
            parsed = datetime.fromisoformat(window_start)
        except ValueError as exc:
            raise ValueError("window_start must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is not None:
            raise ValueError("window_start must not include a timezone")
        return jsonify(
            mcp_client.call_tool(
                "get_anomaly_detail",
                {"sensor_id": sensor_id, "window_start": window_start},
            )
        )

    @app.get("/api/factory-summary")
    def factory_summary():
        return jsonify(mcp_client.call_tool("get_factory_summary"))

    @app.get("/api/dashboard")
    def dashboard_data():
        return jsonify(mcp_client.get_dashboard_overview())

    @app.get("/api/system/mcp")
    def mcp_status():
        try:
            return jsonify(mcp_client.inspect_server())
        except Exception:
            app.logger.exception("MCP runtime inspection failed")
            return jsonify(
                {
                    "available": False,
                    "transport": "stdio",
                    "server": "smart-factory-scada",
                    "mode": "read-only",
                    "database": "TimescaleDB / PostgreSQL",
                    "tool_count": 0,
                    "tools": [],
                    "error": "mcp_unavailable",
                }
            )

    @app.post("/api/ask")
    def ask():
        payload = request.get_json(silent=True) or {}
        question = payload.get("question", "")
        try:
            result = resolve_agent().ask(question)
        except RuntimeError:
            app.logger.exception("RAG agent failed")
            return (
                jsonify(
                    {
                        "error": "rag_unavailable",
                        "message": "AI Agent를 실행하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                    }
                ),
                503,
            )
        return jsonify(result)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=False,
    )
