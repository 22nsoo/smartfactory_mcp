from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from web_app.app import create_app


def main() -> None:
    app = create_app()
    app.testing = True
    client = app.test_client()

    checks = {
        "/": 200,
        "/health": 200,
        "/api/model": 200,
        "/api/sensors": 200,
        "/api/sensors/84/status": 200,
        "/api/sensors/92/history?hours=1&limit=5": 200,
        "/api/abnormal-sensors?minimum_status=DEGRADING": 200,
        "/api/factory-summary": 200,
        "/api/anomaly-detail?sensor_id=92&window_start=2019-07-30%2020:38:00": 200,
        "/api/sensors/not-a-number/status": 400,
        "/api/sensors/92/history?hours=invalid": 400,
    }
    for path, expected in checks.items():
        response = client.get(path)
        if response.status_code != expected:
            raise RuntimeError(f"{path}: expected {expected}, got {response.status_code}: {response.data!r}")
        print(f"{response.status_code} {path}")

    sensors = client.get("/api/sensors").get_json()
    if sensors["sensor_count"] != 3:
        raise RuntimeError(f"Expected 3 sensors, got {sensors}")
    print("Flask smoke test passed")


if __name__ == "__main__":
    main()
