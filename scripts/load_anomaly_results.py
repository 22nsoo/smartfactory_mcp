import argparse
import csv
import io
import json
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from anomaly_common import database_url


RESULT_COLUMNS = [
    "window_start",
    "sensor_id",
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


def parse_args():
    parser = argparse.ArgumentParser(description="Load anomaly results into TimescaleDB.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=Path("data/processed/anomaly/run_summary.json"),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/anomaly/scored_windows.parquet"),
    )
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def postgres_array(values):
    values = list(values)
    if not values:
        return "{}"
    escaped = [str(value).replace("\\", "\\\\").replace('"', '\\"') for value in values]
    return "{" + ",".join(f'"{value}"' for value in escaped) + "}"


def insert_metadata(connection, summary):
    connection.execute(
        """
        INSERT INTO anomaly_model_run (
            model_run_id, source_run_id, feature_names, train_ratio,
            validation_ratio, model_name, model_parameters,
            status_thresholds, sklearn_version, source_summary
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            summary["model_run_id"],
            summary["source_run_id"],
            summary["feature_names"],
            summary["train_ratio"],
            summary["validation_ratio"],
            summary["model_name"],
            Jsonb(summary["model_parameters"]),
            Jsonb(summary["status_thresholds"]),
            summary["sklearn_version"],
            Jsonb(summary),
        ),
    )
    rows = [
        (
            summary["model_run_id"],
            str(sensor["sensor_id"]),
            sensor["train_start"],
            sensor["train_end"],
            sensor["validation_start"],
            sensor["validation_end"],
            sensor["test_start"],
            sensor["test_end"],
            sensor["train_rows"],
            sensor["validation_rows"],
            sensor["test_rows"],
            Jsonb(sensor["sigma_profile"]),
            sensor["validation_severity_p95"],
            sensor["validation_severity_p99"],
            sensor["training_data_hash"],
            sensor["model_path"],
        )
        for sensor in summary["sensors"]
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO anomaly_model_sensor (
                model_run_id, sensor_id, train_start, train_end,
                validation_start, validation_end, test_start, test_end,
                train_rows, validation_rows, test_rows, sigma_profile,
                validation_severity_p95, validation_severity_p99,
                training_data_hash, model_path
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            rows,
        )


def copy_results(connection, input_path, batch_size, expected_run_id):
    parquet = pq.ParquetFile(input_path)
    missing = [name for name in RESULT_COLUMNS if name not in parquet.schema_arrow.names]
    if missing:
        raise ValueError(f"Missing scored columns: {missing}")
    loaded = 0
    copy_sql = """
        COPY anomaly_result (
            window_start, sensor_id, model_run_id, dataset_split,
            sigma_anomaly, sigma_feature_count, sigma_detected_features,
            isolation_decision, isolation_severity, isolation_anomaly,
            risk_score, status
        ) FROM STDIN WITH (FORMAT CSV, NULL '\\N')
    """
    with connection.cursor() as cursor:
        with cursor.copy(copy_sql) as copy:
            for batch in parquet.iter_batches(batch_size=batch_size, columns=RESULT_COLUMNS):
                frame = batch.to_pandas()
                run_ids = set(frame["model_run_id"].astype(str).unique())
                if run_ids != {expected_run_id}:
                    raise ValueError(f"Unexpected model_run_id values: {run_ids}")
                frame["sigma_detected_features"] = frame["sigma_detected_features"].apply(
                    postgres_array
                )
                buffer = io.StringIO()
                frame.to_csv(
                    buffer,
                    index=False,
                    header=False,
                    na_rep="\\N",
                    date_format="%Y-%m-%d %H:%M:%S.%f",
                    lineterminator="\n",
                    quoting=csv.QUOTE_MINIMAL,
                )
                copy.write(buffer.getvalue())
                loaded += len(frame)
                print(f"loaded={loaded:,} rows")
    return loaded


def main():
    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("batch-size must be positive")
    if not args.run_summary.is_file():
        raise FileNotFoundError(args.run_summary)
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    summary = json.loads(args.run_summary.read_text(encoding="utf-8"))
    run_id = summary["model_run_id"]
    expected_rows = pq.ParquetFile(args.input).metadata.num_rows

    with psycopg.connect(database_url(args.database_url)) as connection:
        existing = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM anomaly_model_run WHERE model_run_id = %s)",
            (run_id,),
        ).fetchone()[0]
        if existing and not args.replace:
            raise RuntimeError(f"Model run already exists: {run_id}; use --replace")
        with connection.transaction():
            if existing:
                connection.execute(
                    "DELETE FROM anomaly_model_run WHERE model_run_id = %s", (run_id,)
                )
            insert_metadata(connection, summary)
            loaded = copy_results(connection, args.input, args.batch_size, run_id)
            if loaded != expected_rows:
                raise RuntimeError(f"Expected {expected_rows:,} rows but loaded {loaded:,}")

    print(json.dumps({
        "model_run_id": run_id,
        "expected_rows": expected_rows,
        "loaded_rows": loaded,
    }, indent=2))


if __name__ == "__main__":
    main()
