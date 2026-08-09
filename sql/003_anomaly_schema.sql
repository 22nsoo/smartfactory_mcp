\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS anomaly_model_run (
    model_run_id TEXT PRIMARY KEY,
    source_run_id TEXT REFERENCES eda_run(run_id),
    feature_names TEXT[] NOT NULL,
    train_ratio DOUBLE PRECISION NOT NULL,
    validation_ratio DOUBLE PRECISION NOT NULL,
    model_name TEXT NOT NULL,
    model_parameters JSONB NOT NULL,
    status_thresholds JSONB NOT NULL,
    sklearn_version TEXT NOT NULL,
    source_summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS anomaly_model_sensor (
    model_run_id TEXT NOT NULL REFERENCES anomaly_model_run(model_run_id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    train_start TIMESTAMP NOT NULL,
    train_end TIMESTAMP NOT NULL,
    validation_start TIMESTAMP NOT NULL,
    validation_end TIMESTAMP NOT NULL,
    test_start TIMESTAMP NOT NULL,
    test_end TIMESTAMP NOT NULL,
    train_rows INTEGER NOT NULL,
    validation_rows INTEGER NOT NULL,
    test_rows INTEGER NOT NULL,
    sigma_profile JSONB NOT NULL,
    validation_severity_p95 DOUBLE PRECISION NOT NULL,
    validation_severity_p99 DOUBLE PRECISION NOT NULL,
    training_data_hash TEXT NOT NULL,
    model_path TEXT NOT NULL,
    PRIMARY KEY (model_run_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS anomaly_result (
    window_start TIMESTAMP NOT NULL,
    sensor_id TEXT NOT NULL,
    model_run_id TEXT NOT NULL REFERENCES anomaly_model_run(model_run_id) ON DELETE CASCADE,
    dataset_split TEXT NOT NULL CHECK (dataset_split IN ('train', 'validation', 'test')),
    sigma_anomaly BOOLEAN NOT NULL,
    sigma_feature_count SMALLINT NOT NULL,
    sigma_detected_features TEXT[] NOT NULL,
    isolation_decision DOUBLE PRECISION NOT NULL,
    isolation_severity DOUBLE PRECISION NOT NULL,
    isolation_anomaly BOOLEAN NOT NULL,
    risk_score DOUBLE PRECISION NOT NULL CHECK (risk_score >= 0 AND risk_score <= 100),
    status TEXT NOT NULL CHECK (status IN ('NORMAL', 'ATTENTION', 'DEGRADING', 'WARNING')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sensor_id, window_start, model_run_id)
);

SELECT create_hypertable(
    'anomaly_result',
    'window_start',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS anomaly_result_run_sensor_time_idx
    ON anomaly_result (model_run_id, sensor_id, window_start DESC);

CREATE INDEX IF NOT EXISTS anomaly_result_run_status_time_idx
    ON anomaly_result (model_run_id, status, window_start DESC);
