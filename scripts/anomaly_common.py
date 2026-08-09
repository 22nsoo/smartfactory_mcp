import json
import os
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd


FEATURE_NAMES = [
    "mean",
    "std",
    "min_value",
    "max_value",
    "rms",
    "peak_to_peak",
    "slope",
]

STATUS_THRESHOLDS = {
    "normal_max_exclusive": 60.0,
    "attention_max_exclusive": 80.0,
    "degrading_max_exclusive": 95.0,
    "warning_min_inclusive": 95.0,
    "strong_anomaly_percentile": 99.0,
}


def parse_sensor_ids(raw):
    sensor_ids = [value.strip() for value in raw.split(",") if value.strip()]
    if not sensor_ids:
        raise ValueError("At least one sensor ID is required")
    return sensor_ids


def load_feature_frame(path, sensor_ids):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    columns = [
        "window_start",
        "window_end",
        "sensor_id",
        "unit",
        "sample_count",
        *FEATURE_NAMES,
    ]
    frame = pd.read_parquet(path, columns=columns)
    frame["sensor_id"] = frame["sensor_id"].astype("string")
    frame = frame[frame["sensor_id"].isin(sensor_ids)].copy()
    frame = frame.sort_values(["sensor_id", "window_start"], kind="stable")

    missing_sensors = sorted(set(sensor_ids) - set(frame["sensor_id"].unique()))
    if missing_sensors:
        raise ValueError(f"Sensors missing from input: {missing_sensors}")

    values = frame[FEATURE_NAMES].to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        bad_rows = int((~np.isfinite(values)).any(axis=1).sum())
        raise ValueError(f"Feature input has {bad_rows} rows with NULL or non-finite values")
    if frame.duplicated(["sensor_id", "window_start"]).any():
        raise ValueError("Duplicate sensor_id/window_start rows found")
    return frame.reset_index(drop=True)


def split_counts(row_count, train_ratio, validation_ratio):
    if not 0 < train_ratio < 1:
        raise ValueError("train-ratio must be between 0 and 1")
    if not 0 < validation_ratio < 1:
        raise ValueError("validation-ratio must be between 0 and 1")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train-ratio + validation-ratio must be less than 1")
    train_rows = int(row_count * train_ratio)
    validation_rows = int(row_count * validation_ratio)
    test_rows = row_count - train_rows - validation_rows
    if min(train_rows, validation_rows, test_rows) < 1:
        raise ValueError("Each split must contain at least one row")
    return train_rows, validation_rows, test_rows


def dataset_split_labels(row_count, train_rows, validation_rows):
    labels = np.full(row_count, "test", dtype=object)
    labels[:train_rows] = "train"
    labels[train_rows : train_rows + validation_rows] = "validation"
    return labels


def risk_score_from_reference(severity, sorted_reference):
    reference = np.asarray(sorted_reference, dtype=np.float64)
    if reference.size == 0:
        raise ValueError("Validation severity reference cannot be empty")
    ranks = np.searchsorted(reference, severity, side="right")
    return np.clip(ranks / reference.size * 100.0, 0.0, 100.0)


def statuses_from_risk(risk_score):
    return np.select(
        [risk_score < 60.0, risk_score < 80.0, risk_score < 95.0],
        ["NORMAL", "ATTENTION", "DEGRADING"],
        default="WARNING",
    )


def database_url(explicit_url=None):
    if explicit_url:
        return explicit_url
    required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {missing}")
    user = quote(os.environ["POSTGRES_USER"], safe="")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    database = quote(os.environ["POSTGRES_DB"], safe="")
    port = int(os.environ["POSTGRES_PORT"])
    return f"postgresql://{user}:{password}@localhost:{port}/{database}"


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)

