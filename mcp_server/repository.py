from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row


STATUS_ORDER = {
    "NORMAL": 0,
    "ATTENTION": 1,
    "DEGRADING": 2,
    "WARNING": 3,
}
SENSOR_ID_PATTERN = re.compile(r"^[0-9]{1,10}$")


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def normalize_sensor_id(sensor_id: str | int) -> str:
    normalized = str(sensor_id).strip()
    if not SENSOR_ID_PATTERN.fullmatch(normalized):
        raise ValueError("sensor_id must contain 1 to 10 digits")
    return normalized


def validate_limit(limit: int, maximum: int = 1000) -> int:
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


class SensorRepository:
    """Parameterized, read-only queries over the anomaly result tables."""

    def __init__(self, database_url: str, default_model_run_id: str | None = None):
        self.database_url = database_url
        self.default_model_run_id = default_model_run_id

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _run_id(self, connection, requested: str | None = None) -> str:
        run_id = requested or self.default_model_run_id
        if run_id:
            row = connection.execute(
                "SELECT model_run_id FROM anomaly_model_run WHERE model_run_id = %s",
                (run_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT model_run_id FROM anomaly_model_run ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown model_run_id: {run_id}" if run_id else "No model run found")
        return str(row["model_run_id"])

    @staticmethod
    def _read_only(connection) -> None:
        connection.execute("SET TRANSACTION READ ONLY")

    def list_monitored_sensors(self, model_run_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            rows = connection.execute(
                """
                SELECT
                    ms.sensor_id,
                    ep.unit,
                    ms.train_start,
                    ms.test_end,
                    ms.train_rows + ms.validation_rows + ms.test_rows AS window_count,
                    latest.window_start AS latest_window,
                    latest.status AS latest_status,
                    latest.risk_score AS latest_risk_score
                FROM anomaly_model_sensor AS ms
                LEFT JOIN eda_sensor_profile AS ep
                  ON ep.run_id = (SELECT source_run_id FROM anomaly_model_run WHERE model_run_id = ms.model_run_id)
                 AND ep.sensor_id = ms.sensor_id
                LEFT JOIN LATERAL (
                    SELECT window_start, status, risk_score
                    FROM anomaly_result
                    WHERE model_run_id = ms.model_run_id
                      AND sensor_id = ms.sensor_id
                    ORDER BY window_start DESC
                    LIMIT 1
                ) AS latest ON TRUE
                WHERE ms.model_run_id = %s
                ORDER BY ms.sensor_id::integer
                """,
                (run_id,),
            ).fetchall()
        return json_value({"model_run_id": run_id, "sensor_count": len(rows), "sensors": rows})

    def get_model_summary(self, model_run_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            run = connection.execute(
                """
                SELECT model_run_id, source_run_id, feature_names, train_ratio,
                       validation_ratio, model_name, model_parameters,
                       status_thresholds, sklearn_version, created_at
                FROM anomaly_model_run
                WHERE model_run_id = %s
                """,
                (run_id,),
            ).fetchone()
            bounds = connection.execute(
                """
                SELECT count(*) AS result_count,
                       count(DISTINCT sensor_id) AS sensor_count,
                       min(window_start) AS first_window,
                       max(window_start) AS last_window
                FROM anomaly_result
                WHERE model_run_id = %s
                """,
                (run_id,),
            ).fetchone()
        return json_value({**run, **bounds, "is_historical_data": True})

    def get_sensor_status(
        self, sensor_id: str | int, model_run_id: str | None = None
    ) -> dict[str, Any]:
        sensor = normalize_sensor_id(sensor_id)
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            row = connection.execute(
                """
                SELECT
                    ar.sensor_id, ar.window_start, sf.window_end, sf.unit,
                    ar.risk_score, ar.status, ar.isolation_anomaly,
                    ar.sigma_anomaly, ar.sigma_detected_features,
                    sf.sample_count, sf.mean, sf.std, sf.min_value,
                    sf.max_value, sf.rms, sf.peak_to_peak, sf.slope,
                    previous.window_start AS previous_window,
                    EXTRACT(EPOCH FROM (ar.window_start - previous.window_start)) / 60.0
                        AS gap_minutes
                FROM anomaly_result AS ar
                JOIN sensor_feature_1min AS sf
                  ON sf.sensor_id = ar.sensor_id
                 AND sf.window_start = ar.window_start
                LEFT JOIN LATERAL (
                    SELECT window_start
                    FROM sensor_feature_1min
                    WHERE sensor_id = ar.sensor_id
                      AND window_start < ar.window_start
                    ORDER BY window_start DESC
                    LIMIT 1
                ) AS previous ON TRUE
                WHERE ar.model_run_id = %s AND ar.sensor_id = %s
                ORDER BY ar.window_start DESC
                LIMIT 1
                """,
                (run_id, sensor),
            ).fetchone()
        if row is None:
            raise ValueError(f"Sensor {sensor} is not monitored by model run {run_id}")
        return json_value(
            {
                "model_run_id": run_id,
                "as_of": row["window_start"],
                "is_historical_data": True,
                **row,
            }
        )

    def get_abnormal_sensors(
        self,
        minimum_status: str = "DEGRADING",
        limit: int = 20,
        model_run_id: str | None = None,
    ) -> dict[str, Any]:
        minimum = minimum_status.strip().upper()
        if minimum not in STATUS_ORDER:
            raise ValueError(f"minimum_status must be one of {list(STATUS_ORDER)}")
        limit = validate_limit(limit, maximum=100)
        accepted = [name for name, rank in STATUS_ORDER.items() if rank >= STATUS_ORDER[minimum]]
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (ar.sensor_id)
                        ar.sensor_id, ar.window_start, ar.risk_score, ar.status,
                        ar.sigma_anomaly, ar.sigma_detected_features,
                        sf.unit, sf.sample_count, sf.mean, sf.std, sf.rms,
                        sf.peak_to_peak, sf.slope
                    FROM anomaly_result AS ar
                    JOIN sensor_feature_1min AS sf
                      ON sf.sensor_id = ar.sensor_id
                     AND sf.window_start = ar.window_start
                    WHERE ar.model_run_id = %s
                    ORDER BY ar.sensor_id, ar.window_start DESC
                )
                SELECT * FROM latest
                WHERE status = ANY(%s)
                ORDER BY risk_score DESC, sensor_id
                LIMIT %s
                """,
                (run_id, accepted, limit),
            ).fetchall()
            as_of = connection.execute(
                "SELECT max(window_start) AS value FROM anomaly_result WHERE model_run_id = %s",
                (run_id,),
            ).fetchone()["value"]
        return json_value(
            {
                "model_run_id": run_id,
                "as_of": as_of,
                "is_historical_data": True,
                "minimum_status": minimum,
                "count": len(rows),
                "sensors": rows,
            }
        )

    def get_sensor_history(
        self,
        sensor_id: str | int,
        hours: int = 24,
        limit: int = 200,
        model_run_id: str | None = None,
    ) -> dict[str, Any]:
        sensor = normalize_sensor_id(sensor_id)
        if not 1 <= hours <= 24 * 31:
            raise ValueError("hours must be between 1 and 744")
        limit = validate_limit(limit)
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            latest = connection.execute(
                """
                SELECT max(window_start) AS value
                FROM anomaly_result
                WHERE model_run_id = %s AND sensor_id = %s
                """,
                (run_id, sensor),
            ).fetchone()["value"]
            if latest is None:
                raise ValueError(f"Sensor {sensor} is not monitored by model run {run_id}")
            rows = connection.execute(
                """
                SELECT ar.window_start, sf.window_end, ar.risk_score, ar.status,
                       ar.isolation_anomaly, ar.sigma_anomaly,
                       ar.sigma_detected_features, sf.unit, sf.sample_count,
                       sf.mean, sf.std, sf.min_value, sf.max_value, sf.rms,
                       sf.peak_to_peak, sf.slope
                FROM anomaly_result AS ar
                JOIN sensor_feature_1min AS sf
                  ON sf.sensor_id = ar.sensor_id
                 AND sf.window_start = ar.window_start
                WHERE ar.model_run_id = %s
                  AND ar.sensor_id = %s
                  AND ar.window_start > %s - (%s * INTERVAL '1 hour')
                ORDER BY ar.window_start DESC
                LIMIT %s
                """,
                (run_id, sensor, latest, hours, limit),
            ).fetchall()
        return json_value(
            {
                "model_run_id": run_id,
                "sensor_id": sensor,
                "as_of": latest,
                "is_historical_data": True,
                "requested_hours": hours,
                "returned_count": len(rows),
                "windows": rows,
            }
        )

    def get_anomaly_detail(
        self,
        sensor_id: str | int,
        window_start: str,
        model_run_id: str | None = None,
    ) -> dict[str, Any]:
        sensor = normalize_sensor_id(sensor_id)
        try:
            requested_window = datetime.fromisoformat(window_start)
        except ValueError as exc:
            raise ValueError("window_start must be an ISO-8601 timestamp") from exc
        if requested_window.tzinfo is not None:
            raise ValueError("window_start must not include a timezone; source timestamps are timezone-naive")
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            row = connection.execute(
                """
                SELECT ar.sensor_id, ar.window_start, sf.window_end, sf.unit,
                       ar.dataset_split, ar.risk_score, ar.status,
                       ar.isolation_decision, ar.isolation_severity,
                       ar.isolation_anomaly, ar.sigma_anomaly,
                       ar.sigma_feature_count, ar.sigma_detected_features,
                       sf.sample_count, sf.mean, sf.std, sf.min_value,
                       sf.max_value, sf.rms, sf.peak_to_peak, sf.slope,
                       previous.window_start AS previous_window,
                       EXTRACT(EPOCH FROM (ar.window_start - previous.window_start)) / 60.0
                           AS gap_minutes
                FROM anomaly_result AS ar
                JOIN sensor_feature_1min AS sf
                  ON sf.sensor_id = ar.sensor_id
                 AND sf.window_start = ar.window_start
                LEFT JOIN LATERAL (
                    SELECT window_start
                    FROM sensor_feature_1min
                    WHERE sensor_id = ar.sensor_id
                      AND window_start < ar.window_start
                    ORDER BY window_start DESC
                    LIMIT 1
                ) AS previous ON TRUE
                WHERE ar.model_run_id = %s
                  AND ar.sensor_id = %s
                  AND ar.window_start = %s
                """,
                (run_id, sensor, requested_window),
            ).fetchone()
        if row is None:
            raise ValueError(f"No result for sensor {sensor} at {requested_window.isoformat()}")
        return json_value({"model_run_id": run_id, "is_historical_data": True, **row})

    def get_factory_summary(self, model_run_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            self._read_only(connection)
            run_id = self._run_id(connection, model_run_id)
            row = connection.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (sensor_id)
                        sensor_id, window_start, risk_score, status
                    FROM anomaly_result
                    WHERE model_run_id = %s
                    ORDER BY sensor_id, window_start DESC
                ), bounds AS (
                    SELECT max(window_start) AS as_of
                    FROM anomaly_result
                    WHERE model_run_id = %s
                )
                SELECT
                    bounds.as_of,
                    count(*) AS monitored_sensor_count,
                    count(*) FILTER (WHERE latest.status = 'NORMAL') AS normal_count,
                    count(*) FILTER (WHERE latest.status = 'ATTENTION') AS attention_count,
                    count(*) FILTER (WHERE latest.status = 'DEGRADING') AS degrading_count,
                    count(*) FILTER (WHERE latest.status = 'WARNING') AS warning_count,
                    max(latest.risk_score) AS maximum_latest_risk
                FROM latest CROSS JOIN bounds
                GROUP BY bounds.as_of
                """,
                (run_id, run_id),
            ).fetchone()
        return json_value({"model_run_id": run_id, "is_historical_data": True, **row})
