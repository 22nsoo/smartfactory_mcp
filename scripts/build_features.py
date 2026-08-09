import argparse
import json
import os
from pathlib import Path

import duckdb


def parse_args():
    parser = argparse.ArgumentParser(description="Build time-window SCADA features.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/selected_sensor_readings.parquet"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/sensor_features_1min.parquet"),
    )
    parser.add_argument("--window", default="1 minute")
    parser.add_argument("--min-samples", type=int, default=30)
    return parser.parse_args()


def sql_literal(value):
    return str(value).replace("'", "''")


def main():
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if args.min_samples <= 0:
        raise ValueError("min-samples must be positive")
    normalized_window = args.window.strip().lower()
    if normalized_window not in {"1 minute", "1min", "1m"}:
        raise ValueError("This MVP currently supports a 1-minute window only")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary_output.unlink(missing_ok=True)
    input_path = sql_literal(args.input.resolve())
    output_path = sql_literal(temporary_output.resolve())

    connection = duckdb.connect()
    connection.execute("PRAGMA threads=4")
    connection.execute("PRAGMA memory_limit='2GB'")
    query = f"""
        COPY (
            SELECT
                time_bucket(INTERVAL '1 minute', observed_at) AS window_start,
                time_bucket(INTERVAL '1 minute', observed_at) + INTERVAL '1 minute' AS window_end,
                sensor_id,
                any_value(unit) AS unit,
                count(*)::INTEGER AS sample_count,
                avg(value)::DOUBLE AS mean,
                stddev_pop(value)::DOUBLE AS std,
                min(value)::DOUBLE AS min_value,
                max(value)::DOUBLE AS max_value,
                sqrt(avg(value * value))::DOUBLE AS rms,
                (max(value) - min(value))::DOUBLE AS peak_to_peak,
                regr_slope(value, epoch(observed_at))::DOUBLE AS slope
            FROM read_parquet('{input_path}')
            GROUP BY window_start, window_end, sensor_id
            HAVING count(*) >= {args.min_samples}
            ORDER BY sensor_id, window_start
        ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """

    try:
        connection.execute(query)
        os.replace(temporary_output, args.output)
    except BaseException:
        temporary_output.unlink(missing_ok=True)
        raise

    result = connection.execute(
        f"""
        SELECT
            count(*) AS feature_rows,
            count(DISTINCT sensor_id) AS sensor_count,
            min(window_start) AS start_timestamp,
            max(window_end) AS end_timestamp,
            min(sample_count) AS min_samples,
            avg(sample_count) AS mean_samples,
            max(sample_count) AS max_samples
        FROM read_parquet('{sql_literal(args.output.resolve())}')
        """
    ).fetchone()
    connection.close()

    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "window": "1 minute",
        "minimum_samples": args.min_samples,
        "feature_rows": result[0],
        "sensor_count": result[1],
        "start_timestamp": str(result[2]),
        "end_timestamp": str(result[3]),
        "minimum_observed_samples": result[4],
        "mean_observed_samples": result[5],
        "maximum_observed_samples": result[6],
        "output_size_bytes": args.output.stat().st_size,
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
