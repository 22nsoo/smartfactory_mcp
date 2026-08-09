import argparse
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".matplotlib_cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from anomaly_common import FEATURE_NAMES, write_json


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate label-free anomaly results.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/anomaly/scored_windows.parquet"),
    )
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=Path("data/processed/anomaly/run_summary.json"),
    )
    parser.add_argument(
        "--results-dir", type=Path, default=Path("docs/part2/results")
    )
    parser.add_argument("--images-dir", type=Path, default=Path("docs/part2/images"))
    parser.add_argument("--top-windows", type=int, default=50)
    return parser.parse_args()


def sensor_metrics(test):
    rows = []
    for sensor_id, group in test.groupby("sensor_id", sort=True):
        both = group["sigma_anomaly"] & group["isolation_anomaly"]
        rows.append(
            {
                "sensor_id": sensor_id,
                "test_windows": len(group),
                "sigma_anomaly_count": int(group["sigma_anomaly"].sum()),
                "sigma_anomaly_rate": float(group["sigma_anomaly"].mean()),
                "isolation_anomaly_count": int(group["isolation_anomaly"].sum()),
                "isolation_anomaly_rate": float(group["isolation_anomaly"].mean()),
                "both_anomaly_count": int(both.sum()),
                "both_over_sigma_rate": float(both.sum() / max(group["sigma_anomaly"].sum(), 1)),
                "both_over_isolation_rate": float(
                    both.sum() / max(group["isolation_anomaly"].sum(), 1)
                ),
                "risk_mean": float(group["risk_score"].mean()),
                "risk_p95": float(group["risk_score"].quantile(0.95)),
                "risk_p99": float(group["risk_score"].quantile(0.99)),
                "normal_count": int(group["status"].eq("NORMAL").sum()),
                "attention_count": int(group["status"].eq("ATTENTION").sum()),
                "degrading_count": int(group["status"].eq("DEGRADING").sum()),
                "warning_count": int(group["status"].eq("WARNING").sum()),
            }
        )
    return pd.DataFrame(rows)


def overlap_summary(test):
    rows = []
    for sensor_id, group in test.groupby("sensor_id", sort=True):
        sigma = group["sigma_anomaly"]
        isolation = group["isolation_anomaly"]
        categories = {
            "both": sigma & isolation,
            "sigma_only": sigma & ~isolation,
            "isolation_only": ~sigma & isolation,
            "neither": ~sigma & ~isolation,
        }
        for category, mask in categories.items():
            rows.append(
                {
                    "sensor_id": sensor_id,
                    "category": category,
                    "window_count": int(mask.sum()),
                    "window_rate": float(mask.mean()),
                }
            )
    return pd.DataFrame(rows)


def monthly_summary(frame):
    monthly_frame = frame.assign(month=frame["window_start"].dt.to_period("M").astype(str))
    return (
        monthly_frame.groupby(["sensor_id", "dataset_split", "month"], as_index=False)
        .agg(
            window_count=("window_start", "size"),
            sigma_anomaly_count=("sigma_anomaly", "sum"),
            isolation_anomaly_count=("isolation_anomaly", "sum"),
            warning_count=("status", lambda value: value.eq("WARNING").sum()),
            risk_mean=("risk_score", "mean"),
            risk_p95=("risk_score", lambda value: value.quantile(0.95)),
        )
        .assign(
            sigma_anomaly_rate=lambda value: value["sigma_anomaly_count"]
            / value["window_count"],
            isolation_anomaly_rate=lambda value: value["isolation_anomaly_count"]
            / value["window_count"],
            warning_rate=lambda value: value["warning_count"] / value["window_count"],
        )
    )


def quality_effect_summary(test):
    rows = []
    for sensor_id, group in test.groupby("sensor_id", sort=True):
        group = group.sort_values("window_start").copy()
        group["previous_gap_minutes"] = group["window_start"].diff().dt.total_seconds() / 60.0
        low_sample_limit = float(group["sample_count"].quantile(0.1))
        low_sample = group["sample_count"] <= low_sample_limit
        after_long_gap = group["previous_gap_minutes"] >= 60.0
        rows.append(
            {
                "sensor_id": sensor_id,
                "sample_count_risk_spearman": float(
                    group["sample_count"].corr(group["risk_score"], method="spearman")
                ),
                "sample_count_mean": float(group["sample_count"].mean()),
                "warning_sample_count_mean": float(
                    group.loc[group["isolation_anomaly"], "sample_count"].mean()
                ),
                "low_sample_limit_p10": low_sample_limit,
                "low_sample_windows": int(low_sample.sum()),
                "low_sample_warning_rate": float(
                    group.loc[low_sample, "isolation_anomaly"].mean()
                ),
                "other_sample_warning_rate": float(
                    group.loc[~low_sample, "isolation_anomaly"].mean()
                ),
                "after_60min_gap_windows": int(after_long_gap.sum()),
                "after_60min_gap_warning_rate": float(
                    group.loc[after_long_gap, "isolation_anomaly"].mean()
                )
                if after_long_gap.any()
                else 0.0,
                "other_gap_warning_rate": float(
                    group.loc[~after_long_gap, "isolation_anomaly"].mean()
                ),
            }
        )
    return pd.DataFrame(rows)


def save_plots(frame, test, overlap, monthly, images_dir):
    images_dir.mkdir(parents=True, exist_ok=True)
    for sensor_id, group in frame.groupby("sensor_id", sort=True):
        daily = (
            group.set_index("window_start")
            .resample("1D")
            .agg(risk_mean=("risk_score", "mean"), risk_max=("risk_score", "max"))
            .dropna()
        )
        fig, axis = plt.subplots(figsize=(12, 4))
        axis.plot(daily.index, daily["risk_mean"], label="Daily mean", linewidth=1)
        axis.plot(daily.index, daily["risk_max"], label="Daily max", linewidth=1)
        axis.axhline(95, color="red", linestyle="--", linewidth=1, label="WARNING")
        axis.set(title=f"Sensor {sensor_id} Risk Score", ylabel="Risk Score", ylim=(0, 102))
        axis.legend()
        fig.tight_layout()
        fig.savefig(images_dir / f"sensor_{sensor_id}_score_timeline.png", dpi=150)
        plt.close(fig)

        top = test[test["sensor_id"].eq(sensor_id)].nlargest(50, "risk_score")
        fig, axis = plt.subplots(figsize=(7, 5))
        scatter = axis.scatter(top["rms"], top["peak_to_peak"], c=top["risk_score"], cmap="Reds")
        axis.set(title=f"Sensor {sensor_id} Top Test Anomalies", xlabel="RMS", ylabel="Peak-to-Peak")
        fig.colorbar(scatter, ax=axis, label="Risk Score")
        fig.tight_layout()
        fig.savefig(images_dir / f"sensor_{sensor_id}_top_anomalies.png", dpi=150)
        plt.close(fig)

        selected = overlap[overlap["sensor_id"].eq(sensor_id)]
        fig, axis = plt.subplots(figsize=(7, 4))
        axis.bar(selected["category"], selected["window_count"])
        axis.set(title=f"Sensor {sensor_id} Method Overlap", ylabel="Test windows")
        axis.tick_params(axis="x", rotation=20)
        fig.tight_layout()
        fig.savefig(images_dir / f"sensor_{sensor_id}_method_overlap.png", dpi=150)
        plt.close(fig)

    test_monthly = monthly[monthly["dataset_split"].eq("test")]
    fig, axis = plt.subplots(figsize=(9, 4))
    for sensor_id, group in test_monthly.groupby("sensor_id", sort=True):
        axis.plot(group["month"], group["isolation_anomaly_rate"], marker="o", label=sensor_id)
    axis.set(title="Monthly Test Isolation Anomaly Rate", ylabel="Rate", xlabel="Month")
    axis.legend(title="Sensor")
    fig.tight_layout()
    fig.savefig(images_dir / "monthly_anomaly_rate.png", dpi=150)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8, 4))
    for sensor_id, group in test.groupby("sensor_id", sort=True):
        axis.hist(group["risk_score"], bins=30, alpha=0.45, label=sensor_id)
    axis.set(title="Test Risk Score Distribution", xlabel="Risk Score", ylabel="Windows")
    axis.legend(title="Sensor")
    fig.tight_layout()
    fig.savefig(images_dir / "risk_score_distribution.png", dpi=150)
    plt.close(fig)


def main():
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if not args.run_summary.is_file():
        raise FileNotFoundError(args.run_summary)
    if args.top_windows < 1:
        raise ValueError("top-windows must be positive")
    frame = pd.read_parquet(args.input)
    frame["window_start"] = pd.to_datetime(frame["window_start"])
    test = frame[frame["dataset_split"].eq("test")].copy()
    if test.empty:
        raise ValueError("No test rows found")

    metrics = sensor_metrics(test)
    overlap = overlap_summary(test)
    monthly = monthly_summary(frame)
    quality_effect = quality_effect_summary(test)
    top = (
        test.sort_values(["sensor_id", "risk_score"], ascending=[True, False])
        .groupby("sensor_id", group_keys=False)
        .head(args.top_windows)
        [[
            "sensor_id",
            "window_start",
            "sample_count",
            *FEATURE_NAMES,
            "sigma_anomaly",
            "sigma_detected_features",
            "isolation_decision",
            "risk_score",
            "status",
        ]]
        .copy()
    )
    top["sigma_detected_features"] = top["sigma_detected_features"].apply(
        lambda values: json.dumps(list(values), ensure_ascii=False)
    )

    args.results_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(args.results_dir / "sensor_metrics.csv", index=False)
    overlap.to_csv(args.results_dir / "method_overlap.csv", index=False)
    monthly.to_csv(args.results_dir / "monthly_anomaly_rate.csv", index=False)
    quality_effect.to_csv(args.results_dir / "data_quality_effect.csv", index=False)
    top.to_csv(args.results_dir / "top_anomaly_windows.csv", index=False)
    save_plots(frame, test, overlap, monthly, args.images_dir)
    evaluation_summary = {
        "model_run_id": str(frame["model_run_id"].iloc[0]),
        "total_rows": len(frame),
        "test_rows": len(test),
        "sensor_count": frame["sensor_id"].nunique(),
        "test_sigma_anomaly_count": int(test["sigma_anomaly"].sum()),
        "test_isolation_anomaly_count": int(test["isolation_anomaly"].sum()),
        "test_both_anomaly_count": int(
            (test["sigma_anomaly"] & test["isolation_anomaly"]).sum()
        ),
        "results_directory": str(args.results_dir),
        "images_directory": str(args.images_dir),
    }
    write_json(args.results_dir / "evaluation_summary.json", evaluation_summary)
    model_summary = json.loads(args.run_summary.read_text(encoding="utf-8"))
    write_json(args.results_dir / "model_run_summary.json", model_summary)
    print(json.dumps(evaluation_summary, indent=2))


if __name__ == "__main__":
    main()
