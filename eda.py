import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"id", "value", "unit", "timestamp"}
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def parse_args():
    parser = argparse.ArgumentParser(
        description="EDA for the extracted I-BiDaaS CRF SCADA CSV files."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("SCADA"))
    parser.add_argument(
        "--start-month",
        default="2019_02",
        help="First month to process in YYYY_MM format (default: 2019_02).",
    )
    parser.add_argument(
        "--end-month",
        default="2019_07",
        help="Last month to process in YYYY_MM format (default: 2019_07).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eda_output_2019_02_2019_07"),
    )
    parser.add_argument("--chunk-size", type=int, default=500_000)
    parser.add_argument("--top-sensors", type=int, default=10)
    parser.add_argument("--sample-limit", type=int, default=20_000)
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Only process the first N files (useful for a smoke test).",
    )
    return parser.parse_args()


def detect_separator(path):
    with path.open("rb") as file:
        header = file.readline().decode("utf-8", errors="replace")
    return ";" if header.count(";") > header.count(",") else ","


def select_csv_files(data_dir, start_month, end_month):
    month_pattern = re.compile(r"^\d{4}_(0[1-9]|1[0-2])$")
    if not month_pattern.fullmatch(start_month):
        raise ValueError(f"Invalid start month: {start_month}. Use YYYY_MM format.")
    if not month_pattern.fullmatch(end_month):
        raise ValueError(f"Invalid end month: {end_month}. Use YYYY_MM format.")
    if start_month > end_month:
        raise ValueError("start-month must be earlier than or equal to end-month")

    direct_files = sorted(data_dir.glob("*.csv"))
    if direct_files:
        return direct_files

    month_dirs = sorted(
        path
        for path in data_dir.iterdir()
        if path.is_dir()
        and month_pattern.fullmatch(path.name)
        and start_month <= path.name <= end_month
    )
    return [csv_file for month_dir in month_dirs for csv_file in sorted(month_dir.glob("*.csv"))]


def inspect_file(path):
    separator = detect_separator(path)
    with path.open("rb") as file:
        header = file.readline()
        has_data = bool(file.readline())

    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "separator": separator,
        "status": "pending" if has_data else "header_only",
        "rows": 0,
        "error": "",
    }


def read_chunks(path, separator, chunk_size):
    return pd.read_csv(
        path,
        sep=separator,
        usecols=lambda name: str(name).strip().lower() in REQUIRED_COLUMNS,
        dtype="string",
        chunksize=chunk_size,
        encoding="utf-8",
        encoding_errors="replace",
        on_bad_lines="warn",
        low_memory=False,
    )


def normalize_values(series):
    """Convert the mixed European/English number formats to float64.

    Examples: 0,445 -> 0.445, 117.383 -> 117.383,
    3.796.795 -> 3796.795, 1.234,56 -> 1234.56.
    """
    cleaned = series.astype("string").str.strip()
    cleaned = cleaned.mask(cleaned.eq(""))
    cleaned = cleaned.str.replace(",", ".", regex=False)

    multiple_dots = cleaned.str.count(r"\.").gt(1).fillna(False)
    cleaned.loc[multiple_dots] = cleaned.loc[multiple_dots].str.replace(
        r"\.(?=.*\.)", "", regex=True
    )
    return pd.to_numeric(cleaned, errors="coerce"), multiple_dots


def normalize_chunk(raw):
    chunk = raw.copy()
    chunk.columns = chunk.columns.str.strip().str.lower()
    missing = REQUIRED_COLUMNS - set(chunk.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    chunk = chunk[["id", "value", "unit", "timestamp"]]
    for column in chunk.columns:
        chunk[column] = chunk[column].str.strip()

    raw_value_present = chunk["value"].notna() & chunk["value"].ne("")
    raw_timestamp_present = chunk["timestamp"].notna() & chunk["timestamp"].ne("")

    numeric_value, multiple_dots = normalize_values(chunk["value"])
    parsed_timestamp = pd.to_datetime(
        chunk["timestamp"], format=TIMESTAMP_FORMAT, errors="coerce"
    )

    chunk["value"] = numeric_value
    chunk["timestamp"] = parsed_timestamp
    chunk["unit"] = (
        chunk["unit"]
        .str.replace("�C", "°C", regex=False)
        .str.replace("ï¿½C", "°C", regex=False)
    )

    quality = {
        "invalid_value": int((raw_value_present & numeric_value.isna()).sum()),
        "invalid_timestamp": int(
            (raw_timestamp_present & parsed_timestamp.isna()).sum()
        ),
        "multiple_dot_value": int(multiple_dots.sum()),
    }
    return chunk, quality


def merge_sensor_stats(state, valid):
    grouped = valid.groupby("id", sort=False)["value"]
    aggregate = grouped.agg(["count", "mean", "min", "max"])
    aggregate["variance"] = grouped.var(ddof=0).fillna(0.0)

    for sensor_id, row in aggregate.iterrows():
        batch_count = int(row["count"])
        batch_mean = float(row["mean"])
        batch_m2 = float(row["variance"]) * batch_count
        current = state.get(sensor_id)

        if current is None:
            state[sensor_id] = {
                "count": batch_count,
                "mean": batch_mean,
                "m2": batch_m2,
                "min": float(row["min"]),
                "max": float(row["max"]),
            }
            continue

        old_count = current["count"]
        new_count = old_count + batch_count
        delta = batch_mean - current["mean"]
        current["mean"] += delta * batch_count / new_count
        current["m2"] += (
            batch_m2 + delta * delta * old_count * batch_count / new_count
        )
        current["count"] = new_count
        current["min"] = min(current["min"], float(row["min"]))
        current["max"] = max(current["max"], float(row["max"]))


def first_pass(files, inventory, chunk_size):
    sensor_stats = {}
    sensor_record_counts = Counter()
    sensor_units = defaultdict(set)
    unit_counts = Counter()
    missing_counts = Counter()
    quality_counts = Counter()
    total_rows = 0
    timestamp_min = None
    timestamp_max = None

    print("\n" + "=" * 70)
    print("PASS 1/2: DATASET PROFILE")
    print("=" * 70)

    for file_number, (path, file_info) in enumerate(zip(files, inventory), start=1):
        if file_info["status"] == "header_only":
            continue

        try:
            file_rows = 0
            for raw in read_chunks(path, file_info["separator"], chunk_size):
                raw.columns = raw.columns.str.strip().str.lower()
                missing_columns = REQUIRED_COLUMNS - set(raw.columns)
                if missing_columns:
                    raise ValueError(f"missing columns: {sorted(missing_columns)}")

                file_rows += len(raw)
                total_rows += len(raw)
                for column, count in raw[list(REQUIRED_COLUMNS)].isna().sum().items():
                    missing_counts[column] += int(count)

                chunk, quality = normalize_chunk(raw)
                quality_counts.update(quality)
                sensor_record_counts.update(chunk["id"].dropna().value_counts().to_dict())
                unit_counts.update(chunk["unit"].dropna().value_counts().to_dict())

                pairs = chunk[["id", "unit"]].dropna().drop_duplicates()
                for row in pairs.itertuples(index=False):
                    sensor_units[str(row.id)].add(str(row.unit))

                current_min = chunk["timestamp"].min()
                current_max = chunk["timestamp"].max()
                if pd.notna(current_min):
                    timestamp_min = (
                        current_min
                        if timestamp_min is None
                        else min(timestamp_min, current_min)
                    )
                if pd.notna(current_max):
                    timestamp_max = (
                        current_max
                        if timestamp_max is None
                        else max(timestamp_max, current_max)
                    )

                valid = chunk.dropna(subset=["id", "value"])
                if not valid.empty:
                    merge_sensor_stats(sensor_stats, valid)

            file_info["rows"] = file_rows
            file_info["status"] = "processed"
        except Exception as exc:
            file_info["status"] = "error"
            file_info["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[WARN] {path}: {file_info['error']}")

        if file_number % 10 == 0 or file_number == len(files):
            print(
                f"[{file_number:4d}/{len(files):4d}] "
                f"{total_rows:,} rows | {len(sensor_stats):,} sensors"
            )

    if not sensor_stats:
        raise RuntimeError("No valid sensor values were found. Check file_inventory.csv.")

    rows = []
    for sensor_id, stats in sensor_stats.items():
        count = stats["count"]
        variance = max(stats["m2"] / count, 0.0)
        std = float(np.sqrt(variance))
        mean = stats["mean"]
        rows.append(
            {
                "sensor_id": sensor_id,
                "unit": ", ".join(sorted(sensor_units[sensor_id])),
                "record_count": sensor_record_counts[sensor_id],
                "valid_value_count": count,
                "mean": mean,
                "std": std,
                "min": stats["min"],
                "max": stats["max"],
                "lower_3sigma": mean - 3 * std,
                "upper_3sigma": mean + 3 * std,
            }
        )

    sensor_summary = pd.DataFrame(rows).sort_values(
        "record_count", ascending=False
    ).reset_index(drop=True)

    return {
        "sensor_summary": sensor_summary,
        "unit_counts": unit_counts,
        "missing_counts": missing_counts,
        "quality_counts": quality_counts,
        "total_rows": total_rows,
        "timestamp_min": timestamp_min,
        "timestamp_max": timestamp_max,
    }


def keep_uniform_sample(existing, new_rows, limit, rng):
    if new_rows.empty or limit <= 0:
        return existing
    candidate = new_rows.copy()
    candidate["_random_key"] = rng.random(len(candidate))
    if existing is not None:
        candidate = pd.concat([existing, candidate], ignore_index=True)
    if len(candidate) > limit:
        candidate = candidate.nsmallest(limit, "_random_key")
    return candidate


def update_intervals(sensor_id, timestamps, interval_state, interval_samples, rng, limit):
    values = timestamps.dropna().sort_values().drop_duplicates()
    if values.empty:
        return

    previous = interval_state[sensor_id].get("last")
    seconds = values.astype("int64").to_numpy(dtype=np.int64) / 1_000_000_000
    if previous is not None:
        seconds = np.concatenate(([previous], seconds))
    differences = np.diff(seconds)
    differences = differences[differences > 0]

    state = interval_state[sensor_id]
    if differences.size:
        state["count"] = state.get("count", 0) + int(differences.size)
        state["sum"] = state.get("sum", 0.0) + float(differences.sum())
        state["min"] = min(state.get("min", np.inf), float(differences.min()))
        state["max"] = max(state.get("max", -np.inf), float(differences.max()))
        frame = pd.DataFrame({"seconds": differences})
        interval_samples[sensor_id] = keep_uniform_sample(
            interval_samples.get(sensor_id), frame, limit, rng
        )

    state["last"] = max(previous, seconds[-1]) if previous is not None else seconds[-1]


def second_pass(files, inventory, chunk_size, sensor_summary, top_count, sample_limit):
    top_ids = set(sensor_summary.head(top_count)["sensor_id"])
    thresholds = sensor_summary.set_index("sensor_id")[["lower_3sigma", "upper_3sigma"]]
    lower = thresholds["lower_3sigma"].to_dict()
    upper = thresholds["upper_3sigma"].to_dict()
    outlier_counts = Counter()
    sensor_samples = {}
    interval_state = defaultdict(dict)
    interval_samples = {}
    rng = np.random.default_rng(42)

    print("\n" + "=" * 70)
    print("PASS 2/2: OUTLIERS AND REPRESENTATIVE SAMPLES")
    print("=" * 70)

    for file_number, (path, file_info) in enumerate(zip(files, inventory), start=1):
        if file_info["status"] != "processed":
            continue
        try:
            for raw in read_chunks(path, file_info["separator"], chunk_size):
                chunk, _ = normalize_chunk(raw)
                valid = chunk.dropna(subset=["id", "value"])
                if valid.empty:
                    continue

                row_lower = valid["id"].map(lower)
                row_upper = valid["id"].map(upper)
                outlier_mask = valid["value"].lt(row_lower) | valid["value"].gt(row_upper)
                outlier_counts.update(
                    valid.loc[outlier_mask, "id"].value_counts().to_dict()
                )

                selected = valid[valid["id"].isin(top_ids)]
                for sensor_id, group in selected.groupby("id", sort=False):
                    sample_columns = group[["id", "timestamp", "value", "unit"]]
                    sensor_samples[sensor_id] = keep_uniform_sample(
                        sensor_samples.get(sensor_id),
                        sample_columns,
                        sample_limit,
                        rng,
                    )
                    update_intervals(
                        sensor_id,
                        group["timestamp"],
                        interval_state,
                        interval_samples,
                        rng,
                        sample_limit,
                    )
        except Exception as exc:
            print(f"[WARN] second pass failed for {path}: {type(exc).__name__}: {exc}")

        if file_number % 10 == 0 or file_number == len(files):
            print(f"[{file_number:4d}/{len(files):4d}] second pass")

    for sensor_id, frame in list(sensor_samples.items()):
        sensor_samples[sensor_id] = (
            frame.drop(columns="_random_key").sort_values("timestamp").reset_index(drop=True)
        )

    outlier_rows = []
    for row in sensor_summary.itertuples(index=False):
        outlier_count = outlier_counts[row.sensor_id]
        outlier_rows.append(
            {
                "sensor_id": row.sensor_id,
                "valid_value_count": row.valid_value_count,
                "outlier_count": outlier_count,
                "outlier_ratio": (
                    outlier_count / row.valid_value_count if row.valid_value_count else 0.0
                ),
            }
        )

    interval_rows = []
    for sensor_id, state in interval_state.items():
        count = state.get("count", 0)
        if not count:
            continue
        sample = interval_samples[sensor_id]["seconds"]
        interval_rows.append(
            {
                "sensor_id": sensor_id,
                "interval_count": count,
                "median_interval_sec_approx": float(sample.median()),
                "mean_interval_sec": state["sum"] / count,
                "min_interval_sec": state["min"],
                "max_interval_sec": state["max"],
            }
        )

    outliers = pd.DataFrame(outlier_rows).sort_values(
        "outlier_ratio", ascending=False
    )
    intervals = pd.DataFrame(interval_rows)
    return sensor_samples, outliers, intervals


def save_plots(output_dir, sensor_summary, sensor_samples, sensor_unit_count):
    cache_dir = output_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def safe_name(sensor_id):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(sensor_id))

    for sensor_id, frame in sensor_samples.items():
        row = sensor_summary[sensor_summary["sensor_id"] == sensor_id].iloc[0]
        values = frame["value"].dropna()
        timestamps = frame.dropna(subset=["timestamp", "value"])
        name = safe_name(sensor_id)

        if not values.empty:
            plt.figure(figsize=(10, 5))
            plt.hist(values, bins=50)
            plt.title(f"Sensor {sensor_id} Value Distribution")
            plt.xlabel(f"Value ({row['unit']})")
            plt.ylabel("Frequency")
            plt.tight_layout()
            plt.savefig(output_dir / f"sensor_{name}_histogram.png", dpi=150)
            plt.close()

        if len(timestamps) >= 2:
            plt.figure(figsize=(13, 5))
            plt.plot(timestamps["timestamp"], timestamps["value"], linewidth=0.8)
            plt.axhline(row["mean"], linestyle="--", label="Mean")
            plt.axhline(row["upper_3sigma"], linestyle="--", label="+3σ")
            plt.axhline(row["lower_3sigma"], linestyle="--", label="-3σ")
            plt.title(f"Sensor {sensor_id} Time Series (uniform sample)")
            plt.xlabel("Timestamp")
            plt.ylabel(f"Value ({row['unit']})")
            plt.legend()
            plt.tight_layout()
            plt.savefig(output_dir / f"sensor_{name}_timeseries.png", dpi=150)
            plt.close()

    available = [(sid, frame["value"].dropna()) for sid, frame in sensor_samples.items()]
    available = [(sid, values) for sid, values in available if not values.empty]
    if available:
        plt.figure(figsize=(14, 6))
        plt.boxplot(
            [values for _, values in available],
            tick_labels=[sensor_id for sensor_id, _ in available],
            showfliers=True,
        )
        plt.title("Sensor Value Boxplot (uniform samples)")
        plt.xlabel("Sensor ID")
        plt.ylabel("Value")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(output_dir / "sensor_boxplot.png", dpi=150)
        plt.close()

    top_counts = sensor_summary.head(20)
    if not top_counts.empty:
        plt.figure(figsize=(12, 6))
        plt.bar(top_counts["sensor_id"], top_counts["record_count"])
        plt.title("Top 20 Sensors by Number of Records")
        plt.xlabel("Sensor ID")
        plt.ylabel("Number of Records")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(output_dir / "sensor_record_counts.png", dpi=150)
        plt.close()

    if not sensor_unit_count.empty:
        plt.figure(figsize=(10, 5))
        sensor_unit_count.plot(kind="bar")
        plt.title("Number of Sensors by Unit")
        plt.xlabel("Unit")
        plt.ylabel("Number of Sensors")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(output_dir / "sensor_unit_distribution.png", dpi=150)
        plt.close()


def main():
    args = parse_args()
    if not args.data_dir.is_dir():
        raise FileNotFoundError(f"Data directory not found: {args.data_dir.resolve()}")
    if args.chunk_size <= 0 or args.top_sensors <= 0 or args.sample_limit <= 0:
        raise ValueError("chunk-size, top-sensors, and sample-limit must be positive")

    files = select_csv_files(args.data_dir, args.start_month, args.end_month)
    if args.max_files is not None:
        files = files[: args.max_files]
    if not files:
        raise FileNotFoundError(
            f"No CSV files found for {args.start_month} through {args.end_month} "
            f"under: {args.data_dir.resolve()}"
        )

    inventory = [inspect_file(path) for path in files]
    total_size = sum(item["size_bytes"] for item in inventory)
    header_only_count = sum(item["status"] == "header_only" for item in inventory)
    separator_counts = Counter(item["separator"] for item in inventory)

    print("=" * 70)
    print("I-BiDaaS CRF SCADA EDA")
    print("=" * 70)
    print(f"Data directory: {args.data_dir.resolve()}")
    print(f"Selected months: {args.start_month} through {args.end_month}")
    print(f"CSV files: {len(files):,}")
    print(f"Header-only files: {header_only_count:,}")
    print(f"CSV size: {total_size / (1024 ** 3):.2f} GiB")
    print(f"Separators: {dict(separator_counts)}")

    profile = first_pass(files, inventory, args.chunk_size)
    sensor_summary = profile["sensor_summary"]
    samples, outliers, intervals = second_pass(
        files,
        inventory,
        args.chunk_size,
        sensor_summary,
        args.top_sensors,
        args.sample_limit,
    )

    # 분석이 끝나기 전 중단된 실행이 빈 결과 폴더를 남기지 않도록
    # 두 번의 데이터 순회를 완료한 뒤 출력 디렉터리를 생성한다.
    args.output_dir.mkdir(parents=True, exist_ok=True)

    unit_df = pd.DataFrame(
        profile["unit_counts"].items(), columns=["unit", "record_count"]
    ).sort_values("record_count", ascending=False)
    missing_df = pd.DataFrame(
        [
            {"column": column, "missing_count": profile["missing_counts"][column]}
            for column in sorted(REQUIRED_COLUMNS)
        ]
    )
    missing_df["missing_ratio"] = missing_df["missing_count"] / profile["total_rows"]
    quality_df = pd.DataFrame(
        profile["quality_counts"].items(), columns=["metric", "count"]
    )
    sensor_unit_count = (
        sensor_summary.groupby("unit")["sensor_id"].nunique().sort_values(ascending=False)
    )

    pd.DataFrame(inventory).to_csv(args.output_dir / "file_inventory.csv", index=False)
    sensor_summary.to_csv(args.output_dir / "sensor_summary.csv", index=False)
    unit_df.to_csv(args.output_dir / "unit_distribution.csv", index=False)
    missing_df.to_csv(args.output_dir / "missing_values.csv", index=False)
    quality_df.to_csv(args.output_dir / "data_quality.csv", index=False)
    outliers.to_csv(args.output_dir / "outlier_summary.csv", index=False)
    intervals.to_csv(args.output_dir / "sampling_interval.csv", index=False)

    summary = {
        "data_directory": str(args.data_dir.resolve()),
        "start_month": args.start_month,
        "end_month": args.end_month,
        "csv_file_count": len(files),
        "header_only_file_count": header_only_count,
        "csv_size_bytes": total_size,
        "total_rows": profile["total_rows"],
        "sensor_count": len(sensor_summary),
        "start_timestamp": str(profile["timestamp_min"]),
        "end_timestamp": str(profile["timestamp_max"]),
        "failed_file_count": sum(item["status"] == "error" for item in inventory),
        "limited_run": args.max_files is not None,
    }
    with (args.output_dir / "dataset_summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    save_plots(args.output_dir, sensor_summary, samples, sensor_unit_count)

    print("\n" + "=" * 70)
    print("EDA COMPLETE")
    print("=" * 70)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Results: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
