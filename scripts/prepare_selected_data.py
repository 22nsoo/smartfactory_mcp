import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from eda import detect_separator, normalize_chunk, read_chunks, select_csv_files


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare selected SCADA sensors as Parquet.")
    parser.add_argument("--data-dir", type=Path, default=Path("SCADA"))
    parser.add_argument("--start-month", default="2019_02")
    parser.add_argument("--end-month", default="2019_07")
    parser.add_argument("--sensor-ids", default="92,109,84")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/selected_sensor_readings.parquet"),
    )
    parser.add_argument("--chunk-size", type=int, default=500_000)
    parser.add_argument(
        "--max-files",
        type=int,
        help="Limit input files for a smoke test; omit for the full period.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    sensor_ids = {value.strip() for value in args.sensor_ids.split(",") if value.strip()}
    if not sensor_ids:
        raise ValueError("At least one sensor ID is required")

    files = select_csv_files(args.data_dir, args.start_month, args.end_month)
    if not files:
        raise FileNotFoundError("No source CSV files found")
    if args.max_files is not None:
        if args.max_files < 1:
            raise ValueError("--max-files must be at least 1")
        files = files[: args.max_files]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary_output.unlink(missing_ok=True)

    writer = None
    total_source_rows = 0
    selected_rows = 0
    duplicate_rows = 0
    invalid_value = 0
    invalid_timestamp = 0
    sensor_counts = Counter()
    timestamp_min = None
    timestamp_max = None

    try:
        for file_number, path in enumerate(files, start=1):
            separator = detect_separator(path)
            source_offset = 0

            for raw in read_chunks(path, separator, args.chunk_size):
                source_rows = np.arange(
                    source_offset + 2,
                    source_offset + len(raw) + 2,
                    dtype=np.int64,
                )
                source_offset += len(raw)
                total_source_rows += len(raw)

                chunk, quality = normalize_chunk(raw)
                invalid_value += quality["invalid_value"]
                invalid_timestamp += quality["invalid_timestamp"]
                chunk["source_row"] = source_rows
                chunk = chunk[chunk["id"].isin(sensor_ids)].copy()
                chunk = chunk.dropna(subset=["id", "value", "timestamp"])
                if chunk.empty:
                    continue

                before_deduplication = len(chunk)
                chunk = chunk.drop_duplicates(
                    subset=["id", "value", "unit", "timestamp"], keep="first"
                )
                duplicate_rows += before_deduplication - len(chunk)

                chunk["unit"] = (
                    chunk["unit"]
                    .str.replace("m�/h std.", "m³/h std.", regex=False)
                    .str.replace("m� std.", "m³ std.", regex=False)
                )
                chunk["source_file"] = str(path.relative_to(args.data_dir))
                output_chunk = chunk[
                    ["timestamp", "id", "value", "unit", "source_file", "source_row"]
                ].rename(columns={"timestamp": "observed_at", "id": "sensor_id"})

                current_min = output_chunk["observed_at"].min()
                current_max = output_chunk["observed_at"].max()
                timestamp_min = current_min if timestamp_min is None else min(timestamp_min, current_min)
                timestamp_max = current_max if timestamp_max is None else max(timestamp_max, current_max)
                sensor_counts.update(output_chunk["sensor_id"].value_counts().to_dict())
                selected_rows += len(output_chunk)

                table = pa.Table.from_pandas(output_chunk, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(
                        temporary_output,
                        table.schema,
                        compression="zstd",
                        use_dictionary=["sensor_id", "unit", "source_file"],
                    )
                writer.write_table(table)

            if file_number % 10 == 0 or file_number == len(files):
                print(
                    f"[{file_number:3d}/{len(files):3d}] "
                    f"selected={selected_rows:,} rows"
                )

        if writer is None:
            raise RuntimeError("No rows matched the selected sensors")
        writer.close()
        writer = None
        os.replace(temporary_output, args.output)
    except BaseException:
        if writer is not None:
            writer.close()
        temporary_output.unlink(missing_ok=True)
        raise

    summary = {
        "start_month": args.start_month,
        "end_month": args.end_month,
        "source_file_count": len(files),
        "source_row_count": total_source_rows,
        "selected_sensor_ids": sorted(sensor_ids),
        "selected_row_count": selected_rows,
        "duplicate_rows_removed_within_chunks": duplicate_rows,
        "invalid_value_count": invalid_value,
        "invalid_timestamp_count": invalid_timestamp,
        "start_timestamp": str(timestamp_min),
        "end_timestamp": str(timestamp_max),
        "sensor_row_counts": dict(sorted(sensor_counts.items())),
        "output": str(args.output),
        "output_size_bytes": args.output.stat().st_size,
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
