\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS eda_run (
    run_id TEXT PRIMARY KEY,
    start_month TEXT NOT NULL,
    end_month TEXT NOT NULL,
    csv_file_count INTEGER NOT NULL,
    header_only_file_count INTEGER NOT NULL,
    failed_file_count INTEGER NOT NULL,
    total_rows BIGINT NOT NULL,
    sensor_count INTEGER NOT NULL,
    start_timestamp TIMESTAMP,
    end_timestamp TIMESTAMP,
    source_summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eda_sensor_profile (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    unit TEXT,
    record_count BIGINT NOT NULL,
    valid_value_count BIGINT NOT NULL,
    mean DOUBLE PRECISION,
    std DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    lower_3sigma DOUBLE PRECISION,
    upper_3sigma DOUBLE PRECISION,
    PRIMARY KEY (run_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS eda_quality_metric (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    count BIGINT NOT NULL,
    PRIMARY KEY (run_id, metric)
);

CREATE TABLE IF NOT EXISTS eda_outlier_profile (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    valid_value_count BIGINT NOT NULL,
    outlier_count BIGINT NOT NULL,
    outlier_ratio DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS eda_sampling_profile (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    interval_count BIGINT NOT NULL,
    median_interval_sec_approx DOUBLE PRECISION,
    mean_interval_sec DOUBLE PRECISION,
    min_interval_sec DOUBLE PRECISION,
    max_interval_sec DOUBLE PRECISION,
    PRIMARY KEY (run_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS sensor_reading (
    observed_at TIMESTAMP NOT NULL,
    sensor_id TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit TEXT,
    source_file TEXT NOT NULL,
    source_row BIGINT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable(
    'sensor_reading',
    'observed_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS sensor_reading_sensor_time_idx
    ON sensor_reading (sensor_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS sensor_feature_1min (
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    sensor_id TEXT NOT NULL,
    unit TEXT,
    sample_count INTEGER NOT NULL,
    mean DOUBLE PRECISION,
    std DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    rms DOUBLE PRECISION,
    peak_to_peak DOUBLE PRECISION,
    slope DOUBLE PRECISION,
    source_run_id TEXT REFERENCES eda_run(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sensor_id, window_start)
);

SELECT create_hypertable(
    'sensor_feature_1min',
    'window_start',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS sensor_feature_sensor_time_idx
    ON sensor_feature_1min (sensor_id, window_start DESC);
