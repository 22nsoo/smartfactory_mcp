# Part 1 — EDA와 TimescaleDB

[문서 홈](../README.md) · [프로젝트 홈](../../README.md) · 다음: [Part 2](../part2/README.md)

31GB SCADA 데이터에서 6개월 구간을 선정해 EDA를 수행하고, 센서 `92`, `109`, `84`의 원시값과 1분 Feature를 TimescaleDB에 적재한 기록이다.

## 문서

- [블로그 원고](smart_factory_mcp_blog_part1.md)
- [EDA → TimescaleDB 실행 가이드](eda_to_timescaledb_runbook.md)

## 결과 스냅샷

`results/`에는 완료된 EDA의 CSV와 JSON을 보관한다. `images/`에는 EDA에서 생성한 PNG를 보관한다.

이 파일들은 블로그 기록용 스냅샷이다. 실행 스크립트는 프로젝트 루트의 `scripts/`, DB 스키마는 `sql/`, 재생성 가능한 전체 출력은 `eda_output_2019_02_2019_07/`에 유지한다.

대용량 원본과 실행 데이터는 중복 복사하지 않는다.

- `SCADA/`: 원본 CSV
- `data/processed/`: 선정 센서 Parquet
- Docker `timescale_data` 볼륨: TimescaleDB 데이터
- `backups/smart_factory.dump`: DB 백업

## 관련 코드

- `eda.py`: 대용량 CSV EDA
- `scripts/prepare_selected_data.py`: 선택 센서 Parquet 생성
- `scripts/build_features.py`: 1분 Feature 생성
- `scripts/load_timescaledb.py`: EDA·원시값·Feature 적재
- `sql/001_extensions.sql`, `sql/002_schema.sql`: TimescaleDB schema
