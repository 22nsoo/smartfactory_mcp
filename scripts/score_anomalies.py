import argparse
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from anomaly_common import (
    FEATURE_NAMES,
    dataset_split_labels,
    load_feature_frame,
    risk_score_from_reference,
    statuses_from_risk,
    write_json,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Score SCADA feature windows.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/sensor_features_1min.parquet"),
    )
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=Path("data/processed/anomaly/run_summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/anomaly/scored_windows.parquet"),
    )
    return parser.parse_args()


def sigma_results(values, profile):
    lower = np.array([profile[name]["lower"] for name in FEATURE_NAMES])
    upper = np.array([profile[name]["upper"] for name in FEATURE_NAMES])
    detected = (values < lower) | (values > upper)
    feature_lists = [
        [name for name, flag in zip(FEATURE_NAMES, row, strict=True) if flag]
        for row in detected
    ]
    return detected.any(axis=1), detected.sum(axis=1), feature_lists


def main():
    args = parse_args()
    if not args.run_summary.is_file():
        raise FileNotFoundError(args.run_summary)
    summary = json.loads(args.run_summary.read_text(encoding="utf-8"))
    sensor_ids = [str(value) for value in summary["sensor_ids"]]
    frame = load_feature_frame(args.input, sensor_ids)
    scored_parts = []

    for sensor_summary in summary["sensors"]:
        sensor_id = str(sensor_summary["sensor_id"])
        sensor = frame[frame["sensor_id"].eq(sensor_id)].reset_index(drop=True)
        artifact = joblib.load(sensor_summary["model_path"])
        if artifact["feature_names"] != FEATURE_NAMES:
            raise ValueError(f"Feature mismatch for sensor {sensor_id}")
        if len(sensor) != sensor_summary["row_count"]:
            raise ValueError(f"Row count changed for sensor {sensor_id}")

        values = sensor[FEATURE_NAMES].to_numpy(dtype=np.float64)
        sigma_anomaly, sigma_count, sigma_features = sigma_results(
            values, artifact["sigma_profile"]
        )
        decision = artifact["model"].decision_function(values)
        severity = -decision
        risk_score = risk_score_from_reference(
            severity, artifact["validation_severity_sorted"]
        )
        sensor["model_run_id"] = summary["model_run_id"]
        sensor["dataset_split"] = dataset_split_labels(
            len(sensor), artifact["train_rows"], artifact["validation_rows"]
        )
        sensor["sigma_anomaly"] = sigma_anomaly
        sensor["sigma_feature_count"] = sigma_count.astype(np.int16)
        sensor["sigma_detected_features"] = sigma_features
        sensor["isolation_decision"] = decision
        sensor["isolation_severity"] = severity
        sensor["isolation_anomaly"] = risk_score >= 95.0
        sensor["risk_score"] = risk_score
        sensor["status"] = statuses_from_risk(risk_score)
        scored_parts.append(sensor)
        print(
            f"sensor={sensor_id} windows={len(sensor):,} "
            f"warning={(risk_score >= 95.0).sum():,}"
        )

    scored = pd.concat(scored_parts, ignore_index=True)
    output_columns = [
        "window_start",
        "window_end",
        "sensor_id",
        "unit",
        "sample_count",
        *FEATURE_NAMES,
        "model_run_id",
        "dataset_split",
        "sigma_anomaly",
        "sigma_feature_count",
        "sigma_detected_features",
        "isolation_decision",
        "isolation_severity",
        "isolation_anomaly",
        "risk_score",
        "status",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    scored[output_columns].to_parquet(
        temporary, index=False, compression="zstd", engine="pyarrow"
    )
    os.replace(temporary, args.output)
    score_summary = {
        "model_run_id": summary["model_run_id"],
        "row_count": len(scored),
        "sensor_count": scored["sensor_id"].nunique(),
        "start_timestamp": str(scored["window_start"].min()),
        "end_timestamp": str(scored["window_start"].max()),
        "sigma_anomaly_count": int(scored["sigma_anomaly"].sum()),
        "isolation_anomaly_count": int(scored["isolation_anomaly"].sum()),
        "status_counts": {
            str(key): int(value) for key, value in scored["status"].value_counts().items()
        },
        "output": str(args.output),
        "output_size_bytes": args.output.stat().st_size,
    }
    write_json(args.output.with_suffix(".summary.json"), score_summary)
    print(json.dumps(score_summary, indent=2))


if __name__ == "__main__":
    main()

