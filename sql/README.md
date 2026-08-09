# TimescaleDB Schema

EDA metadata, SCADA 원시값, 1분 Feature와 이상탐지 결과를 저장하는 PostgreSQL/TimescaleDB schema입니다.

## 적용 순서

```text
001_extensions.sql
→ 002_schema.sql
→ 003_anomaly_schema.sql
```

| 파일 | 생성 대상 |
|---|---|
| `001_extensions.sql` | TimescaleDB extension |
| `002_schema.sql` | EDA profile, `sensor_reading`, `sensor_feature_1min` |
| `003_anomaly_schema.sql` | model run metadata와 `anomaly_result` |

`sensor_reading`, `sensor_feature_1min`, `anomaly_result`는 시간 컬럼을 기준으로 hypertable을 생성합니다. 조회용 index는 센서·모델·상태와 timestamp 조합으로 구성합니다.

## 적용 예시

```bash
docker compose up -d
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/001_extensions.sql
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/002_schema.sql
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/003_anomaly_schema.sql
```

데이터 적재 명령과 검증 SQL은 [Part 1 Runbook](../docs/part1/eda_to_timescaledb_runbook.md)과 [Part 2 Runbook](../docs/part2/anomaly_detection_runbook.md)에 정리되어 있습니다.
