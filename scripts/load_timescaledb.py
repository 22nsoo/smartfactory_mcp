import argparse
import csv
import io
import json
import os
from pathlib import Path
from urllib.parse import quote

import duckdb
import pandas as pd
import psycopg
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb


def parse_args():
    parser = argparse.ArgumentParser(description="Load SCADA EDA and Parquet into TimescaleDB.")
    parser.add_argument("--database-url", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    eda = subparsers.add_parser("eda")
    eda.add_argument("--run-id", required=True)
    eda.add_argument("--input-dir", type=Path, required=True)

    readings = subparsers.add_parser("readings")
    readings.add_argument("--input", type=Path, required=True)
    readings.add_argument("--batch-size", type=int, default=100_000)
    readings.add_argument("--replace", action="store_true")

    features = subparsers.add_parser("features")
    features.add_argument("--run-id", required=True)
    features.add_argument("--input", type=Path, required=True)
    features.add_argument("--batch-size", type=int, default=100_000)
    features.add_argument("--replace", action="store_true")
    return parser.parse_args()


def database_url(explicit_url):
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


def null_if_nan(value):
    return None if pd.isna(value) else value


def load_eda(connection, args):
    source = args.input_dir
    required = [
        "dataset_summary.json",
        "sensor_summary.csv",
        "data_quality.csv",
        "outlier_summary.csv",
        "sampling_interval.csv",
    ]
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing EDA files: {missing}")

    summary = json.loads((source / "dataset_summary.json").read_text(encoding="utf-8"))
    sensor = pd.read_csv(source / "sensor_summary.csv", dtype={"sensor_id": "string"})
    quality = pd.read_csv(source / "data_quality.csv")
    outlier = pd.read_csv(source / "outlier_summary.csv", dtype={"sensor_id": "string"})
    sampling = pd.read_csv(source / "sampling_interval.csv", dtype={"sensor_id": "string"})

    with connection.transaction():
        connection.execute(
            """
            INSERT INTO eda_run (
                run_id, start_month, end_month, csv_file_count,
                header_only_file_count, failed_file_count, total_rows,
                sensor_count, start_timestamp, end_timestamp, source_summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                start_month = EXCLUDED.start_month,
                end_month = EXCLUDED.end_month,
                csv_file_count = EXCLUDED.csv_file_count,
                header_only_file_count = EXCLUDED.header_only_file_count,
                failed_file_count = EXCLUDED.failed_file_count,
                total_rows = EXCLUDED.total_rows,
                sensor_count = EXCLUDED.sensor_count,
                start_timestamp = EXCLUDED.start_timestamp,
                end_timestamp = EXCLUDED.end_timestamp,
                source_summary = EXCLUDED.source_summary
            """,
            (
                args.run_id,
                summary["start_month"],
                summary["end_month"],
                summary["csv_file_count"],
                summary["header_only_file_count"],
                summary["failed_file_count"],
                summary["total_rows"],
                summary["sensor_count"],
                summary["start_timestamp"],
                summary["end_timestamp"],
                Jsonb(summary),
            ),
        )
        for table in (
            "eda_sensor_profile",
            "eda_quality_metric",
            "eda_outlier_profile",
            "eda_sampling_profile",
        ):
            connection.execute(f"DELETE FROM {table} WHERE run_id = %s", (args.run_id,))

        sensor_rows = [
            (
                args.run_id,
                str(row.sensor_id),
                null_if_nan(row.unit),
                int(row.record_count),
                int(row.valid_value_count),
                null_if_nan(row.mean),
                null_if_nan(row.std),
                null_if_nan(row.min),
                null_if_nan(row.max),
                null_if_nan(row.lower_3sigma),
                null_if_nan(row.upper_3sigma),
            )
            for row in sensor.itertuples(index=False)
        ]
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO eda_sensor_profile (
                    run_id, sensor_id, unit, record_count, valid_value_count,
                    mean, std, min_value, max_value, lower_3sigma, upper_3sigma
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                sensor_rows,
            )
            cursor.executemany(
                "INSERT INTO eda_quality_metric (run_id, metric, count) VALUES (%s, %s, %s)",
                [
                    (args.run_id, str(row.metric), int(row.count))
                    for row in quality.itertuples(index=False)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO eda_outlier_profile (
                    run_id, sensor_id, valid_value_count, outlier_count, outlier_ratio
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (
                        args.run_id,
                        str(row.sensor_id),
                        int(row.valid_value_count),
                        int(row.outlier_count),
                        float(row.outlier_ratio),
                    )
                    for row in outlier.itertuples(index=False)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO eda_sampling_profile (
                    run_id, sensor_id, interval_count, median_interval_sec_approx,
                    mean_interval_sec, min_interval_sec, max_interval_sec
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        args.run_id,
                        str(row.sensor_id),
                        int(row.interval_count),
                        null_if_nan(row.median_interval_sec_approx),
                        null_if_nan(row.mean_interval_sec),
                        null_if_nan(row.min_interval_sec),
                        null_if_nan(row.max_interval_sec),
                    )
                    for row in sampling.itertuples(index=False)
                ],
            )

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "sensor_profiles": len(sensor_rows),
                "quality_metrics": len(quality),
                "outlier_profiles": len(outlier),
                "sampling_profiles": len(sampling),
            },
            indent=2,
        )
    )


def parquet_bounds(path, timestamp_column):
    escaped = str(path.resolve()).replace("'", "''")
    connection = duckdb.connect()
    result = connection.execute(
        f"""
        SELECT
            count(*),
            min({timestamp_column}),
            max({timestamp_column}),
            list_sort(list(DISTINCT sensor_id))
        FROM read_parquet('{escaped}')
        """
    ).fetchone()
    connection.close()
    return result


def copy_parquet(connection, path, columns, copy_sql, batch_size, extra_column=None):
    parquet = pq.ParquetFile(path)
    loaded = 0
    with connection.cursor() as cursor:
        with cursor.copy(copy_sql) as copy:
            for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
                frame = batch.to_pandas()
                if extra_column is not None:
                    name, value = extra_column
                    frame[name] = value
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
                if loaded % 1_000_000 < len(frame):
                    print(f"loaded={loaded:,} rows")
    return loaded


def load_readings(connection, args):
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    count, start, end, sensors = parquet_bounds(args.input, "observed_at")
    existing = connection.execute(
        """
        SELECT count(*) FROM sensor_reading
        WHERE sensor_id = ANY(%s) AND observed_at BETWEEN %s AND %s
        """,
        (sensors, start, end),
    ).fetchone()[0]
    if existing and not args.replace:
        raise RuntimeError(f"{existing:,} matching readings already exist; use --replace")

    with connection.transaction():
        if existing:
            connection.execute(
                """
                DELETE FROM sensor_reading
                WHERE sensor_id = ANY(%s) AND observed_at BETWEEN %s AND %s
                """,
                (sensors, start, end),
            )
        loaded = copy_parquet(
            connection,
            args.input,
            ["observed_at", "sensor_id", "value", "unit", "source_file", "source_row"],
            """
            COPY sensor_reading (
                observed_at, sensor_id, value, unit, source_file, source_row
            ) FROM STDIN WITH (FORMAT CSV, NULL '\\N')
            """,
            args.batch_size,
        )
    print(json.dumps({"expected": count, "loaded": loaded, "sensors": sensors}, indent=2))


def load_features(connection, args):
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    count, start, end, sensors = parquet_bounds(args.input, "window_start")
    existing = connection.execute(
        """
        SELECT count(*) FROM sensor_feature_1min
        WHERE sensor_id = ANY(%s) AND window_start BETWEEN %s AND %s
        """,
        (sensors, start, end),
    ).fetchone()[0]
    if existing and not args.replace:
        raise RuntimeError(f"{existing:,} matching features already exist; use --replace")

    with connection.transaction():
        if existing:
            connection.execute(
                """
                DELETE FROM sensor_feature_1min
                WHERE sensor_id = ANY(%s) AND window_start BETWEEN %s AND %s
                """,
                (sensors, start, end),
            )
        loaded = copy_parquet(
            connection,
            args.input,
            [
                "window_start",
                "window_end",
                "sensor_id",
                "unit",
                "sample_count",
                "mean",
                "std",
                "min_value",
                "max_value",
                "rms",
                "peak_to_peak",
                "slope",
            ],
            """
            COPY sensor_feature_1min (
                window_start, window_end, sensor_id, unit, sample_count,
                mean, std, min_value, max_value, rms, peak_to_peak, slope,
                source_run_id
            ) FROM STDIN WITH (FORMAT CSV, NULL '\\N')
            """,
            args.batch_size,
            extra_column=("source_run_id", args.run_id),
        )
    print(json.dumps({"expected": count, "loaded": loaded, "sensors": sensors}, indent=2))


def main():
    args = parse_args()
    url = database_url(args.database_url)
    with psycopg.connect(url) as connection:
        if args.command == "eda":
            load_eda(connection, args)
        elif args.command == "readings":
            load_readings(connection, args)
        elif args.command == "features":
            load_features(connection, args)


if __name__ == "__main__":
    main()
