from __future__ import annotations

import os
from typing import Any

import psycopg
from flask import Flask, jsonify, render_template, request

from mcp_server.repository import SensorRepository
from mcp_server.server import DEFAULT_MODEL_RUN_ID, database_url_from_environment


def integer_query(name: str, default: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def create_app(repository: SensorRepository | None = None, rag_agent=None) -> Flask:
    app = Flask(__name__)
    repo = repository or SensorRepository(
        database_url_from_environment(),
        default_model_run_id=os.getenv("MCP_MODEL_RUN_ID", DEFAULT_MODEL_RUN_ID),
    )
    agent_holder = {"value": rag_agent}

    def resolve_agent():
        if agent_holder["value"] is None:
            from rag.workflow import build_agent

            agent_holder["value"] = build_agent()
        return agent_holder["value"]

    @app.errorhandler(ValueError)
    def invalid_request(error: ValueError):
        return jsonify({"error": "invalid_request", "message": str(error)}), 400

    @app.errorhandler(psycopg.Error)
    def database_error(error: psycopg.Error):
        app.logger.exception("Database query failed")
        return jsonify({"error": "database_unavailable", "message": "Database query failed"}), 503

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/health")
    def health():
        summary = repo.get_model_summary()
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
        return jsonify(repo.get_model_summary())

    @app.get("/api/sensors")
    def monitored_sensors():
        return jsonify(repo.list_monitored_sensors())

    @app.get("/api/sensors/<sensor_id>/status")
    def sensor_status(sensor_id: str):
        return jsonify(repo.get_sensor_status(sensor_id))

    @app.get("/api/sensors/<sensor_id>/history")
    def sensor_history(sensor_id: str):
        return jsonify(
            repo.get_sensor_history(
                sensor_id=sensor_id,
                hours=integer_query("hours", 24),
                limit=integer_query("limit", 200),
            )
        )

    @app.get("/api/abnormal-sensors")
    def abnormal_sensors():
        return jsonify(
            repo.get_abnormal_sensors(
                minimum_status=request.args.get("minimum_status", "DEGRADING"),
                limit=integer_query("limit", 20),
            )
        )

    @app.get("/api/anomaly-detail")
    def anomaly_detail():
        sensor_id = request.args.get("sensor_id", "")
        window_start = request.args.get("window_start", "")
        if not window_start:
            raise ValueError("window_start is required")
        return jsonify(repo.get_anomaly_detail(sensor_id, window_start))

    @app.get("/api/factory-summary")
    def factory_summary():
        return jsonify(repo.get_factory_summary())

    @app.post("/api/ask")
    def ask():
        payload = request.get_json(silent=True) or {}
        question = payload.get("question", "")
        try:
            result = resolve_agent().ask(question)
        except RuntimeError as error:
            app.logger.exception("RAG agent failed")
            return jsonify({"error": "rag_unavailable", "message": str(error)}), 503
        return jsonify(result)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=False,
    )
