import argparse
import hashlib
import json
import shutil
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from pandas.util import hash_pandas_object
from sklearn.ensemble import IsolationForest

from anomaly_common import (
    FEATURE_NAMES,
    STATUS_THRESHOLDS,
    load_feature_frame,
    parse_sensor_ids,
    split_counts,
    write_json,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Train per-sensor SCADA anomaly models.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/sensor_features_1min.parquet"),
    )
    parser.add_argument("--sensor-ids", default="92,109,84")
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--validation-ratio", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--run-id", default="iforest_2019_02_2019_07_v1")
    parser.add_argument("--source-run-id", default="eda_2019_02_2019_07_v1")
    parser.add_argument("--output-dir", type=Path, default=Path("models"))
    parser.add_argument(
        "--run-summary-output",
        type=Path,
        default=Path("data/processed/anomaly/run_summary.json"),
    )
    parser.add_argument(
        "--split-summary-output",
        type=Path,
        default=Path("docs/part2/results/split_summary.csv"),
    )
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def training_hash(frame):
    hashed = hash_pandas_object(
        frame[["window_start", *FEATURE_NAMES]], index=False
    ).to_numpy(dtype=np.uint64)
    return hashlib.sha256(hashed.tobytes()).hexdigest()


def sigma_profile(train):
    profile = {}
    for feature in FEATURE_NAMES:
        mean = float(train[feature].mean())
        std = float(train[feature].std(ddof=0))
        profile[feature] = {
            "mean": mean,
            "std": std,
            "lower": mean - 3.0 * std,
            "upper": mean + 3.0 * std,
        }
    return profile


def main():
    args = parse_args()
    if args.n_estimators < 1:
        raise ValueError("n-estimators must be positive")
    sensor_ids = parse_sensor_ids(args.sensor_ids)
    frame = load_feature_frame(args.input, sensor_ids)
    run_dir = args.output_dir / args.run_id
    if run_dir.exists():
        if not args.replace:
            raise FileExistsError(f"Model run already exists: {run_dir}; use --replace")
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    model_parameters = {
        "n_estimators": args.n_estimators,
        "contamination": "auto",
        "random_state": args.random_state,
        "n_jobs": -1,
    }
    sensor_summaries = []
    split_rows = []

    for sensor_id in sensor_ids:
        sensor = frame[frame["sensor_id"].eq(sensor_id)].reset_index(drop=True)
        train_rows, validation_rows, test_rows = split_counts(
            len(sensor), args.train_ratio, args.validation_ratio
        )
        validation_end_index = train_rows + validation_rows
        train = sensor.iloc[:train_rows]
        validation = sensor.iloc[train_rows:validation_end_index]
        test = sensor.iloc[validation_end_index:]

        model = IsolationForest(**model_parameters)
        model.fit(train[FEATURE_NAMES].to_numpy(dtype=np.float64))
        validation_severity = -model.decision_function(
            validation[FEATURE_NAMES].to_numpy(dtype=np.float64)
        )
        sorted_validation = np.sort(validation_severity.astype(np.float64))
        profile = sigma_profile(train)
        model_path = run_dir / f"sensor_{sensor_id}.joblib"
        artifact = {
            "model": model,
            "model_run_id": args.run_id,
            "sensor_id": sensor_id,
            "feature_names": FEATURE_NAMES,
            "sigma_profile": profile,
            "validation_severity_sorted": sorted_validation,
            "train_rows": train_rows,
            "validation_rows": validation_rows,
            "test_rows": test_rows,
            "training_data_hash": training_hash(train),
            "sklearn_version": sklearn.__version__,
        }
        joblib.dump(artifact, model_path, compress=3)

        sensor_summary = {
            "sensor_id": sensor_id,
            "row_count": len(sensor),
            "train_rows": train_rows,
            "validation_rows": validation_rows,
            "test_rows": test_rows,
            "train_start": str(train["window_start"].iloc[0]),
            "train_end": str(train["window_start"].iloc[-1]),
            "validation_start": str(validation["window_start"].iloc[0]),
            "validation_end": str(validation["window_start"].iloc[-1]),
            "test_start": str(test["window_start"].iloc[0]),
            "test_end": str(test["window_start"].iloc[-1]),
            "sigma_profile": profile,
            "validation_severity_p95": float(np.quantile(validation_severity, 0.95)),
            "validation_severity_p99": float(np.quantile(validation_severity, 0.99)),
            "training_data_hash": artifact["training_data_hash"],
            "model_path": str(model_path),
            "model_size_bytes": model_path.stat().st_size,
        }
        sensor_summaries.append(sensor_summary)
        split_rows.append(
            {
                key: sensor_summary[key]
                for key in (
                    "sensor_id",
                    "row_count",
                    "train_rows",
                    "validation_rows",
                    "test_rows",
                    "train_start",
                    "train_end",
                    "validation_start",
                    "validation_end",
                    "test_start",
                    "test_end",
                )
            }
        )
        print(
            f"sensor={sensor_id} train={train_rows:,} validation={validation_rows:,} "
            f"test={test_rows:,}"
        )

    summary = {
        "model_run_id": args.run_id,
        "source_run_id": args.source_run_id,
        "source_input": str(args.input),
        "source_row_count": len(frame),
        "sensor_ids": sensor_ids,
        "feature_names": FEATURE_NAMES,
        "sample_count_policy": "quality filter only; minimum 30 in source feature data",
        "train_ratio": args.train_ratio,
        "validation_ratio": args.validation_ratio,
        "test_ratio": 1.0 - args.train_ratio - args.validation_ratio,
        "model_name": "IsolationForest",
        "model_parameters": model_parameters,
        "status_thresholds": STATUS_THRESHOLDS,
        "sklearn_version": sklearn.__version__,
        "sensors": sensor_summaries,
    }
    write_json(run_dir / "run_summary.json", summary)
    write_json(args.run_summary_output, summary)
    args.split_summary_output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(split_rows).to_csv(args.split_summary_output, index=False)
    print(json.dumps({
        "model_run_id": args.run_id,
        "sensor_count": len(sensor_summaries),
        "run_summary": str(args.run_summary_output),
        "model_directory": str(run_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
