# SCADA 6개월 EDA → TimescaleDB 적재 실행 가이드

## 1. 목적과 범위

이 문서는 다음 작업을 재현 가능한 순서로 수행하기 위한 실행 가이드다.

1. `2019_02`부터 `2019_07`까지 6개월 EDA 실행
2. EDA 결과 검토 및 분석 대상 센서 선정
3. 선정 센서의 원시 데이터 정제
4. 1분 Window Feature 생성
5. TimescaleDB 실행 및 스키마 생성
6. EDA 결과, 선정 센서 원시 데이터, Feature 적재
7. 적재 결과 검증 및 백업

이 문서의 전 과정은 2026-08-09 실행과 검증을 완료했으며, 실제 결과값을 각 절에 기록했다.

> 아래 명령은 프로젝트 루트 `smartfactory_mcp/`에서 실행한다.

---

## 2. 기본 원칙

- 분석 기간: `2019_02`~`2019_07`
- 대상 CSV: 180개
- 데이터 포함 파일: 157개
- 입력 크기: 약 6.69GiB
- 원본 CSV는 수정하지 않는다.
- 이상 후보값은 학습 전에 임의 삭제하지 않는다.
- `timestamp`는 원본에 시간대가 없으므로 우선 `TIMESTAMP WITHOUT TIME ZONE`으로 보존한다.
- EDA 결과를 근거로 MVP 센서 ID를 `92,109,84`로 고정한다.
- TimescaleDB에는 6개월 전체 센서 원시값을 바로 넣지 않는다.
- EDA에서 선정한 센서의 원시값과 1분 Feature만 먼저 적재한다.

---

## 3. 현재 환경

확인된 로컬 환경:

```text
OS: macOS 15, Apple Silicon
Python: 프로젝트 .venv 사용
PostgreSQL: 18.3 설치됨, 현재 중지 상태
TimescaleDB: 설치되지 않음
Docker: 설치되지 않음
로컬 PostgreSQL 기본 포트: 5432
TimescaleDB 포트: 5433
```

TimescaleDB는 공식 Docker 이미지 `timescale/timescaledb:2.29.0-pg18`을 사용한다. 버전이 자동으로 바뀌는 `latest` 계열 대신 버전을 고정한다.

공식 자료:

- [TimescaleDB Docker 이미지](https://hub.docker.com/r/timescale/timescaledb)
- [TimescaleDB Docker 저장소](https://github.com/timescale/timescaledb-docker)
- [TimescaleDB 설치 문서](https://docs.timescale.com/self-hosted/latest/install/installation-docker/)

---

## 4. 전체 실행 순서

```text
환경 및 디스크 확인
        ↓
6개월 EDA 실행
        ↓
EDA 품질 검증
        ↓
센서 3~5개 선정
        ↓
정제 Parquet 생성
        ↓
1분 Window Feature 생성
        ↓
TimescaleDB 실행
        ↓
스키마 생성
        ↓
EDA 결과 적재
        ↓
선정 센서 원시값 및 Feature 적재
        ↓
SQL 검증 및 백업
```

---

# Part A. EDA 실행과 결과 확정

## 5. 실행 전 확인

프로젝트 루트로 이동한다.

```bash
cd /Users/insu/Desktop/smartfactory_mcp
```

Python과 필수 패키지를 확인한다.

```bash
.venv/bin/python --version
.venv/bin/python -c "import pandas, numpy, matplotlib; print('EDA dependencies OK')"
```

입력 데이터와 디스크 공간을 확인한다.

```bash
du -sh SCADA/2019_{02,03,04,05,06,07}
df -h .
```

이전 6개월 EDA 결과가 존재하는지 확인한다.

```bash
find . -maxdepth 1 -type d -name 'eda_output_2019_02_2019_07' -print
```

## 6. 6개월 EDA 실행

기본 설정이 이미 `2019_02`~`2019_07`이므로 다음 명령을 실행한다.

```bash
.venv/bin/python eda.py
```

명시적으로 실행하려면 다음 명령을 사용한다.

```bash
.venv/bin/python eda.py \
  --data-dir SCADA \
  --start-month 2019_02 \
  --end-month 2019_07 \
  --output-dir eda_output_2019_02_2019_07 \
  --chunk-size 500000 \
  --top-sensors 10 \
  --sample-limit 20000
```

로그를 파일로 함께 보관하려면 다음과 같이 실행한다.

```bash
.venv/bin/python eda.py 2>&1 | tee eda_2019_02_2019_07.log
```

> EDA는 6.69GiB를 두 번 순회한다. 실행 중 강제 종료하면 결과 폴더가 생성되지 않거나 일부 저장 단계만 남을 수 있으므로, 재실행 전 결과 폴더 상태를 확인한다.

## 7. EDA 실행 완료 확인

필수 결과 파일을 확인한다.

```bash
find eda_output_2019_02_2019_07 -maxdepth 1 -type f | sort
```

요약 결과를 확인한다.

```bash
sed -n '1,160p' eda_output_2019_02_2019_07/dataset_summary.json
sed -n '1,80p' eda_output_2019_02_2019_07/data_quality.csv
sed -n '1,30p' eda_output_2019_02_2019_07/sensor_summary.csv
sed -n '1,30p' eda_output_2019_02_2019_07/outlier_summary.csv
sed -n '1,30p' eda_output_2019_02_2019_07/sampling_interval.csv
```

파일 처리 오류를 확인한다.

```bash
rg ',error,' eda_output_2019_02_2019_07/file_inventory.csv
```

`rg` 결과가 없으면 `status=error`인 파일이 없는지 다음 명령으로 다시 확인한다.

```bash
.venv/bin/python -c "import pandas as pd; p='eda_output_2019_02_2019_07/file_inventory.csv'; d=pd.read_csv(p); print(d['status'].value_counts()); print(d[d['status'].eq('error')].to_string(index=False))"
```

## 8. EDA 결과 기록

| 항목 | 결과 |
|---|---|
| 실행 상태 | 완료, 2026-08-09 재검증 |
| 분석 기간 | 2019_02~2019_07 |
| 처리 CSV 수 | 180 |
| 헤더 전용 파일 수 | 23 |
| 실패 파일 수 | 0 |
| 전체 행 수 | 184,664,873 |
| 실제 센서 수 | 149 |
| 시작 Timestamp | 2019-01-31 00:00:00.143 |
| 종료 Timestamp | 2019-07-30 20:43:10.127 |
| `invalid_value` | 0 |
| `invalid_timestamp` | 0 |
| `multiple_dot_value` | 0 |

### 단위별 센서 수

| 단위 | 센서 수 | 비고 |
|---|---:|---|
| mg | 88 | 전체 레코드의 85.69% |
| mm/s | 30 | 속도 진동 센서 |
| °C | 9 | 온도 센서 |
| bar | 8 | 압력 센서 |
| l/min | 8 | 유량 센서 |
| 기타 | 6 | l, Nl/min, mm, 손상 단위 포함 |

### 주요 데이터 품질 문제

```text
선택 기간에는 숫자와 Timestamp 변환 실패가 없었다.
단위 문자열 m�/h std., m� std.는 인코딩 손상 상태로 남아 있다.
주말 비가동으로 추정되는 약 3일의 긴 수집 공백이 존재한다.
```

### 숫자 정규화 검증 결과

`3.796.795 → 3796.795` 규칙이 센서 분포상 타당한지 기록한다.

```text
선택한 6개월에는 multiple_dot_value가 0건이므로 해당 규칙은 실제 값에 적용되지 않았다.
향후 다른 기간을 추가할 때 원본 문자열과 센서별 분포를 다시 검증한다.
```

## 9. DB 진행 조건

다음 조건을 확인한 후 정제와 DB 적재로 진행한다.

- [x] 실패 파일을 모두 확인했거나 허용 사유를 기록함
- [x] 숫자 변환 실패율을 확인함
- [x] Timestamp 변환 실패율을 확인함
- [x] 다중 점 숫자 정규화 규칙을 검증함
- [x] 센서별 단위가 일관적인지 확인함
- [x] 데이터가 지나치게 적거나 상수인 센서를 제외함
- [x] 분석 대상 센서 3~5개를 선정함
- [x] 선정 센서가 분석 기간 전반에 존재함

## 10. 선정 센서 기록 — EDA 후 작성

초기 MVP는 `mg` 또는 `mm/s` 단위의 진동 센서를 우선한다.

| 센서 ID | 단위 | 유효 행 수 | 데이터 존재 기간 | 중앙 수집 간격 | 선정 이유 |
|---|---|---:|---|---:|---|
| 92 | mg | 11,315,162 | 전체 기간 | 약 0.657초 | 최다 관측치, 강한 Spike 비교 대상 |
| 109 | mg | 11,216,992 | 전체 기간 | 약 0.657초 | 낮은 진폭군 비교 대상 |
| 84 | mg | 10,712,815 | 전체 기간 | 약 0.657초 | 상대적으로 낮은 3-Sigma 비율 |

선정 센서 ID:

```text
SELECTED_SENSOR_IDS=92,109,84
```

---

# Part B. 정제 데이터와 Feature 생성

## 11. 추가 Python 패키지

Parquet과 PostgreSQL 적재에 필요한 패키지를 설치한다.

```bash
.venv/bin/python -m pip install -r requirements-storage.txt
```

설치를 확인한다.

```bash
.venv/bin/python -c "import pyarrow, psycopg, sqlalchemy; print('storage dependencies OK')"
```

## 12. 권장 디렉터리

```text
data/
├── processed/
│   ├── selected_sensor_readings.parquet
│   └── sensor_features_1min.parquet
sql/
├── 001_extensions.sql
└── 002_schema.sql
scripts/
├── prepare_selected_data.py
├── build_features.py
└── load_timescaledb.py
backups/
```

정제 파일과 DB 백업은 크기가 커질 수 있으므로 Git에서 제외한다.

```gitignore
/data/processed/
/backups/
*.dump
```

## 13. 정제 스크립트 요구사항

`scripts/prepare_selected_data.py`는 `eda.py`와 동일한 파싱 규칙을 재사용해야 한다.

- `2019_02`~`2019_07` 폴더만 처리
- 파일별 구분자 자동 감지
- 컬럼명을 `id`, `value`, `unit`, `timestamp`로 통일
- 혼합 소수점 표기 정규화
- 손상된 단위 문자열을 확인하고 알려진 표기를 정규화
- EDA에서 선정한 센서만 필터링
- `id`, `value`, `timestamp` 변환 실패 행 제외 및 건수 기록
- 완전히 동일한 중복 행 제거
- 원본 파일명과 원본 행 번호 보존
- 메모리에 전체 데이터를 올리지 않고 Chunk별로 단일 ZSTD Parquet에 기록

실행 명령:

```bash
.venv/bin/python scripts/prepare_selected_data.py \
  --data-dir SCADA \
  --start-month 2019_02 \
  --end-month 2019_07 \
  --sensor-ids "92,109,84" \
  --output data/processed/selected_sensor_readings.parquet
```

실행 결과 33,244,953행, 약 222MiB의 Parquet을 생성했고 완전 중복 16행을 제거했다.

## 14. 정제 Parquet 검증

```bash
.venv/bin/python -c "import duckdb; print(duckdb.sql(\"SELECT sensor_id, count(*) AS rows, count(*)-count(value) AS null_value, count(*)-count(observed_at) AS null_time, min(observed_at), max(observed_at) FROM read_parquet('data/processed/selected_sensor_readings.parquet') GROUP BY sensor_id ORDER BY sensor_id\").df())"
```

필수 검증 항목:

- [x] 선택하지 않은 센서가 포함되지 않음
- [x] `observed_at`과 `value`에 결측이 없음
- [x] 센서별 단위가 일관됨
- [x] 시간 범위가 선정 기간과 일치함
- [x] 원본 파일명과 행 번호가 보존됨
- [x] 중복 제거 16건이 기록됨

## 15. 1분 Window Feature

초기 Feature는 다음으로 제한한다.

```text
sample_count
mean
std
min
max
rms
peak_to_peak
slope
```

`scripts/build_features.py` 실행 명령:

```bash
.venv/bin/python scripts/build_features.py \
  --input data/processed/selected_sensor_readings.parquet \
  --output data/processed/sensor_features_1min.parquet \
  --window 1min \
  --min-samples 30
```

정상 수집 구간에서 1분당 약 91개 관측치가 예상되어 최소 샘플 수를 30으로 정했다. 실행 결과 387,741개 Window가 생성됐고 Window당 평균 관측치는 85.44개였다.

---

# Part C. TimescaleDB 준비

## 16. 디스크 확인

TimescaleDB 설치 전에 확인한다.

```bash
df -h .
du -sh SCADA data/processed 2>/dev/null
```

전체 6개월 원시 데이터를 적재하지 않고 선정 센서만 적재하더라도 최소 15GiB 이상의 여유 공간을 유지한다. 공간이 부족하면 원시값 DB 적재를 중단하고 Feature와 EDA 요약만 적재한다.

## 17. Docker Desktop 설치

Docker가 없다면 다음 명령으로 설치한다.

```bash
brew install --cask docker
```

Docker Desktop을 실행한다.

```bash
open -a Docker
```

Docker가 준비될 때까지 기다린 후 확인한다.

```bash
docker version
docker compose version
docker info
```

## 18. 환경변수 파일

프로젝트 루트의 `.env`에 다음 값을 저장한다. `.env`는 Git에 포함하지 않는다.

```dotenv
POSTGRES_DB=smart_factory
POSTGRES_USER=smart_factory
POSTGRES_PASSWORD=<충분히 긴 로컬 개발용 비밀번호>
POSTGRES_PORT=5433
```

공유 가능한 `.env.example`에는 실제 비밀번호 대신 예시만 둔다.

```dotenv
POSTGRES_DB=smart_factory
POSTGRES_USER=smart_factory
POSTGRES_PASSWORD=change_me
POSTGRES_PORT=5433
```

## 19. Docker Compose 정의

프로젝트 루트의 `docker-compose.yml`에 다음 내용을 사용한다.

```yaml
services:
  timescaledb:
    image: timescale/timescaledb:2.29.0-pg18
    container_name: smart_factory_timescaledb
    restart: unless-stopped
    env_file:
      - .env
    environment:
      TIMESCALEDB_TELEMETRY: "off"
      TS_TUNE_MEMORY: "2GB"
      TS_TUNE_NUM_CPUS: "2"
    ports:
      - "${POSTGRES_PORT:-5433}:5432"
    volumes:
      - timescale_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 12

volumes:
  timescale_data:
```

로컬 PostgreSQL의 5432 포트와 충돌하지 않도록 TimescaleDB는 5433 포트를 사용한다.

## 20. TimescaleDB 시작과 확인

이미지를 내려받고 DB를 시작한다.

```bash
docker compose pull
docker compose up -d
```

상태와 로그를 확인한다.

```bash
docker compose ps
docker compose logs --tail=100 timescaledb
```

접속을 확인한다.

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory -c "SELECT version();"
```

TimescaleDB 확장을 활성화하고 확인한다.

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
```

---

# Part D. DB 스키마

## 21. 확장 SQL

`sql/001_extensions.sql`:

```sql
\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS timescaledb;
```

## 22. 테이블 SQL

`sql/002_schema.sql`:

```sql
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
    run_id TEXT NOT NULL REFERENCES eda_run(run_id),
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
    run_id TEXT NOT NULL REFERENCES eda_run(run_id),
    metric TEXT NOT NULL,
    count BIGINT NOT NULL,
    PRIMARY KEY (run_id, metric)
);

CREATE TABLE IF NOT EXISTS eda_outlier_profile (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id),
    sensor_id TEXT NOT NULL,
    valid_value_count BIGINT NOT NULL,
    outlier_count BIGINT NOT NULL,
    outlier_ratio DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, sensor_id)
);

CREATE TABLE IF NOT EXISTS eda_sampling_profile (
    run_id TEXT NOT NULL REFERENCES eda_run(run_id),
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
```

스키마를 적용한다.

```bash
docker compose exec -T timescaledb \
  psql -U smart_factory -d smart_factory \
  < sql/001_extensions.sql

docker compose exec -T timescaledb \
  psql -U smart_factory -d smart_factory \
  < sql/002_schema.sql
```

Hypertable을 확인한다.

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables ORDER BY hypertable_name;"
```

---

# Part E. 데이터 적재

## 23. 적재 전략

적재 순서는 다음과 같다.

1. `dataset_summary.json` → `eda_run`
2. `sensor_summary.csv` → `eda_sensor_profile`
3. `data_quality.csv` → `eda_quality_metric`
4. `outlier_summary.csv` → `eda_outlier_profile`
5. `sampling_interval.csv` → `eda_sampling_profile`
6. 선정 센서 Parquet → `sensor_reading`
7. 1분 Feature Parquet → `sensor_feature_1min`

`file_inventory.csv`는 파일별 상세 진단용으로 우선 파일 상태로 보관하고, 운영 요구가 생기면 별도 DB 테이블을 추가한다.

## 24. 적재 스크립트 요구사항

`scripts/load_timescaledb.py`는 다음 기능을 제공해야 한다.

- 환경변수의 DB 접속 정보 사용
- 하나의 `run_id`로 EDA 결과 연결
- EDA CSV 컬럼과 DB 컬럼 매핑
- psycopg `COPY`를 이용한 batch 적재
- 트랜잭션 실패 시 전체 rollback
- 적재 전후 행 수 기록
- 같은 `run_id`의 중복 EDA 적재 방지
- 원시값 중복 적재 방지 또는 사전 중복 검사
- 실패 행을 별도 로그로 저장
- 비밀번호를 로그에 출력하지 않음

환경변수를 로드한다.

```bash
set -a
source .env
set +a
```

EDA 결과 적재 명령:

```bash
.venv/bin/python scripts/load_timescaledb.py eda \
  --run-id "eda_2019_02_2019_07_v1" \
  --input-dir eda_output_2019_02_2019_07
```

선정 센서 원시값 적재 명령:

```bash
.venv/bin/python scripts/load_timescaledb.py readings \
  --input data/processed/selected_sensor_readings.parquet \
  --batch-size 100000
```

Feature 적재 명령:

```bash
.venv/bin/python scripts/load_timescaledb.py features \
  --run-id "eda_2019_02_2019_07_v1" \
  --input data/processed/sensor_features_1min.parquet \
  --batch-size 100000
```

> 세 스크립트는 구현과 스모크 테스트를 마쳤다. 재실행 시 기존 범위가 감지되므로 원시값과 Feature를 교체하려면 명시적으로 `--replace`를 사용한다.

---

# Part F. 적재 검증

## 25. 기본 검증 SQL

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory
```

접속 후 다음 SQL을 실행한다.

```sql
SELECT *
FROM eda_run
ORDER BY created_at DESC;

SELECT unit, count(*) AS sensor_count
FROM eda_sensor_profile
WHERE run_id = 'eda_2019_02_2019_07_v1'
GROUP BY unit
ORDER BY sensor_count DESC;

SELECT metric, count
FROM eda_quality_metric
WHERE run_id = 'eda_2019_02_2019_07_v1'
ORDER BY metric;

SELECT
    sensor_id,
    count(*) AS row_count,
    min(observed_at) AS first_seen,
    max(observed_at) AS last_seen,
    min(value) AS min_value,
    max(value) AS max_value
FROM sensor_reading
GROUP BY sensor_id
ORDER BY sensor_id;

SELECT
    sensor_id,
    count(*) AS window_count,
    min(window_start) AS first_window,
    max(window_start) AS last_window
FROM sensor_feature_1min
GROUP BY sensor_id
ORDER BY sensor_id;

SELECT hypertable_name, num_chunks
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;
```

DB 크기를 확인한다.

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT
    hypertable_name,
    pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name))) AS size
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;
```

## 26. 적재 완료 기록

| 항목 | 결과 |
|---|---|
| EDA Run ID | `eda_2019_02_2019_07_v1` |
| TimescaleDB 버전 | 2.29.0 |
| PostgreSQL 버전 | 18.4 |
| 적재 센서 ID | 92, 109, 84 |
| `eda_sensor_profile` 행 수 | 149 |
| `sensor_reading` 행 수 | 33,244,953 |
| `sensor_feature_1min` 행 수 | 387,741 |
| 원시 데이터 시작 시각 | 2019-01-31 00:00:00.363 |
| 원시 데이터 종료 시각 | 2019-07-30 20:43:10.127 |
| DB 전체 크기 | 8,271MB |
| Hypertable 크기 | 원시값 8,161MB / Feature 99MB |
| Chunk 수 | 원시값 26 / Feature 7 |
| 적재 실패 행 수 | 0 |
| 백업 파일 | `backups/smart_factory.dump`, 392MB |
| 검증 일시 | 2026-08-09 |

---

# Part G. 운영 명령

## 27. 시작, 중지, 로그

시작:

```bash
docker compose up -d
```

중지:

```bash
docker compose stop
```

재시작:

```bash
docker compose restart timescaledb
```

로그:

```bash
docker compose logs -f --tail=100 timescaledb
```

상태:

```bash
docker compose ps
```

## 28. 백업과 복구

백업 디렉터리를 만든다.

```bash
mkdir -p backups
```

Custom-format 백업:

```bash
docker compose exec -T timescaledb \
  pg_dump -U smart_factory -d smart_factory -Fc \
  > backups/smart_factory.dump
```

백업 파일을 확인한다.

```bash
ls -lh backups/smart_factory.dump
```

2026-08-09 실행 결과 `backups/smart_factory.dump`가 392MB로 생성됐고,
`pg_restore --list`로 archive 목록을 정상 확인했다.

복구는 빈 DB에서 수행한다.

```bash
docker compose exec -T timescaledb \
  pg_restore -U smart_factory -d smart_factory \
  --clean --if-exists < backups/smart_factory.dump
```

> `pg_restore --clean`은 기존 객체를 제거할 수 있으므로 복구 대상 DB를 반드시 확인한 후 실행한다.

## 29. 데이터 삭제 주의사항

다음 명령은 컨테이너만 제거하고 DB 볼륨은 유지한다.

```bash
docker compose down
```

다음 명령은 DB 볼륨까지 삭제하므로 일반 작업에서는 실행하지 않는다.

```text
docker compose down -v
```

---

# Part H. 완료 기준

## 30. 최종 체크리스트

### EDA

- [x] 6개월 EDA가 오류 없이 완료됨
- [x] EDA 결과 표가 작성됨
- [x] 숫자 정규화 규칙이 검증됨
- [x] 분석 센서 3~5개가 선정됨

### 정제 및 Feature

- [x] 선정 센서 Parquet이 생성됨
- [x] 결측·중복·시간 범위가 검증됨
- [x] 1분 Feature가 생성됨
- [x] Feature Window 기준이 기록됨

### TimescaleDB

- [x] Docker TimescaleDB가 정상 실행됨
- [x] TimescaleDB 확장이 활성화됨
- [x] EDA 결과가 적재됨
- [x] 선정 센서 원시값이 적재됨
- [x] 1분 Feature가 적재됨
- [x] 파일과 DB 행 수가 일치함
- [x] DB 크기와 디스크 여유를 확인함
- [x] 백업 파일을 생성하고 확인함

---

## 31. 다음 단계

이 문서의 완료 조건을 충족한 후 다음 단계로 진행한다.

```text
3-Sigma Baseline
→ Isolation Forest 학습
→ anomaly_result 저장
→ MCP 조회 Tool 구현
```
