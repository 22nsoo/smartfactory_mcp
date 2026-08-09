from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from mcp_server.repository import SensorRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_RUN_ID = "iforest_2019_02_2019_07_v1"


def database_url_from_environment() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing database settings: {', '.join(missing)}")
    user = quote(os.environ["POSTGRES_USER"], safe="")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    database = quote(os.environ["POSTGRES_DB"], safe="")
    port = int(os.environ["POSTGRES_PORT"])
    return (
        f"postgresql://{user}:{password}"
        f"@localhost:{port}/{database}"
    )


def build_server(repository: SensorRepository | None = None) -> MCPServer:
    repo = repository or SensorRepository(
        database_url_from_environment(),
        default_model_run_id=os.getenv("MCP_MODEL_RUN_ID", DEFAULT_MODEL_RUN_ID),
    )
    server = MCPServer(
        name="smart-factory-scada",
        title="Smart Factory SCADA Monitor",
        description="Read-only access to historical SCADA anomaly results in TimescaleDB.",
        instructions=(
            "This server exposes historical 2019 SCADA results for sensors 92, 109, and 84. "
            "Never describe the latest stored row as a real-time factory reading; use the returned as_of value."
        ),
        version="0.1.0",
    )

    @server.tool(structured_output=True)
    def list_monitored_sensors() -> dict[str, Any]:
        """List sensors included in the fixed Part 2 model run and their latest stored states."""
        return repo.list_monitored_sensors()

    @server.tool(structured_output=True)
    def get_model_summary() -> dict[str, Any]:
        """Return model metadata, feature names, result count, and historical data bounds."""
        return repo.get_model_summary()

    @server.tool(structured_output=True)
    def get_sensor_status(sensor_id: str) -> dict[str, Any]:
        """Return the latest stored anomaly status, features, sample count, and collection gap for one sensor."""
        return repo.get_sensor_status(sensor_id)

    @server.tool(structured_output=True)
    def get_abnormal_sensors(
        minimum_status: str = "DEGRADING", limit: int = 20
    ) -> dict[str, Any]:
        """Return sensors whose latest stored state is at or above the requested status level."""
        return repo.get_abnormal_sensors(minimum_status=minimum_status, limit=limit)

    @server.tool(structured_output=True)
    def get_sensor_history(
        sensor_id: str, hours: int = 24, limit: int = 200
    ) -> dict[str, Any]:
        """Return recent windows relative to that sensor's last historical timestamp, newest first."""
        return repo.get_sensor_history(sensor_id=sensor_id, hours=hours, limit=limit)

    @server.tool(structured_output=True)
    def get_anomaly_detail(sensor_id: str, window_start: str) -> dict[str, Any]:
        """Return model scores, detected 3-Sigma features, window features, and preceding data gap."""
        return repo.get_anomaly_detail(sensor_id=sensor_id, window_start=window_start)

    @server.tool(structured_output=True)
    def get_factory_summary() -> dict[str, Any]:
        """Summarize the latest stored state counts across all monitored sensors."""
        return repo.get_factory_summary()

    return server


mcp = build_server()


if __name__ == "__main__":
    mcp.run(transport="stdio")
