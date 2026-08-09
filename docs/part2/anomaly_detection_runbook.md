# SCADA 1분 Feature → 이상탐지 실행 가이드

## 1. 목적

이 문서는 Part 1에서 생성한 `sensor_feature_1min` 데이터를 이용해 다음 과정을 재현하기 위한 실행 가이드다.

1. TimescaleDB 입력 데이터 검증
2. 센서별 시간 순서 Train/Validation/Test 분리
3. 3-Sigma Baseline 생성
4. Isolation Forest 학습
5. Validation 기반 Risk Score 보정
6. Test 결과와 모델 간 일치도 검증
7. 결과 시각화
8. TimescaleDB 적재

> 아래 명령은 프로젝트 루트 `smartfactory_mcp/`에서 실행한다.

---

## 2. 확정된 입력

| 항목 | 값 |
|---|---|
| Feature 테이블 | `sensor_feature_1min` |
| 센서 ID | 92, 109, 84 |
| 전체 Feature 행 수 | 387,741 |
| Window | 1분 |
| 최소 관측치 | 30 |
| Feature 기간 | 2019-01-31~2019-07-30 |

초기 모델 입력:

```text
mean,std,min_value,max_value,rms,peak_to_peak,slope
```

`sample_count`는 품질 검사에 사용하고 초기 모델 입력에서는 제외한다.

---

## 3. 실행 전 확인

Docker DB 상태:

```bash
docker compose ps
```

Feature 행 수:

```bash
docker compose exec -T timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "SELECT sensor_id, count(*), min(window_start), max(window_start) FROM sensor_feature_1min GROUP BY sensor_id ORDER BY sensor_id;"
```

완료 조건:

- [x] TimescaleDB가 `healthy` 상태다.
- [x] 센서 92, 109, 84가 모두 존재한다.
- [x] 전체 Feature 행 수가 387,741개다.
- [x] 모델 Feature에 NULL 또는 무한대가 없는지 확인했다.
- [x] 같은 센서와 `window_start`의 중복이 없다.

---

## 4. Python 환경

설치 명령:

```bash
.venv/bin/python -m pip install -r requirements-ml.txt
```

실행 환경은 Scikit-learn 1.9.0과 Joblib 1.5.3이며 기존 `pandas`, `numpy`, `matplotlib`, `psycopg`, `pyarrow`도 사용한다.

권장 requirements 파일:

```text
requirements-ml.txt
```

---

## 5. 파일 구조

구현된 파일 구조:

```text
scripts/
├── anomaly_common.py
├── train_anomaly_models.py
├── score_anomalies.py
├── evaluate_anomalies.py
└── load_anomaly_results.py

sql/
└── 003_anomaly_schema.sql

models/
└── iforest_2019_02_2019_07_v1/
    ├── sensor_92.joblib
    ├── sensor_109.joblib
    └── sensor_84.joblib

data/processed/anomaly/
├── scored_windows.parquet
└── run_summary.json
```

모델과 생성 Parquet은 `.gitignore` 대상에 추가하고, 재현에 필요한 파라미터와 결과 요약만 `docs/part2/results/`에 보관한다.

---

## 6. 데이터 추출과 시간 분리

각 센서를 `window_start` 기준으로 정렬하고 행 수 기준 `60/20/20`으로 나눈다.

```text
Train         0%  ~ 60%
Validation   60%  ~ 80%
Test         80%  ~ 100%
```

규칙:

- 센서별로 독립 분리한다.
- 동일 Timestamp를 서로 다른 Split에 나누지 않는다.
- 전처리기는 Train에만 `fit()`한다.
- Validation과 Test에는 Train에서 확정한 변환만 적용한다.
- 수집 공백은 보간하지 않는다.

실행 명령:

```bash
.venv/bin/python scripts/train_anomaly_models.py \
  --input data/processed/sensor_features_1min.parquet \
  --sensor-ids 92,109,84 \
  --train-ratio 0.6 \
  --validation-ratio 0.2 \
  --random-state 42 \
  --n-estimators 300 \
  --run-id iforest_2019_02_2019_07_v1 \
  --source-run-id eda_2019_02_2019_07_v1 \
  --output-dir models
```

### 분리 결과

| 센서 | Train 행 수 | Validation 행 수 | Test 행 수 | 경계 Timestamp |
|---|---:|---:|---:|---|
| 92 | 78,765 | 26,255 | 26,255 | 05-20 23:48 / 06-25 14:31 |
| 109 | 78,756 | 26,252 | 26,252 | 05-21 00:00 / 06-25 14:45 |
| 84 | 75,123 | 25,041 | 25,042 | 05-21 20:04 / 06-26 13:43 |

---

## 7. 3-Sigma Baseline

Train 구간에서 센서·Feature별 다음 값을 저장한다.

```text
mean
std
lower_3sigma
upper_3sigma
```

Window별 결과:

```text
sigma_anomaly
sigma_feature_count
sigma_detected_features
```

3-Sigma는 분포 가정을 만족하는 최종 모델이 아니라 Isolation Forest 비교 기준이다.

---

## 8. Isolation Forest

초기 파라미터:

```text
n_estimators=300
contamination=auto
random_state=42
n_jobs=-1
```

모델은 센서별로 학습하고 다음 정보를 함께 저장한다.

```text
sensor_id
feature_names
train_start
train_end
row_count
library_version
model_parameters
training_data_hash
```

원본 점수:

```text
isolation_decision = decision_function(X)
isolation_severity = -isolation_decision
```

---

## 9. Risk Score 보정

Validation의 `isolation_severity` 경험적 누적분포를 기준으로 Risk Score를 생성한다.

```text
risk_score = Validation ECDF Percentile × 100
```

초기 상태 등급:

| Risk Score | 상태 |
|---:|---|
| 0 이상 60 미만 | NORMAL |
| 60 이상 80 미만 | ATTENTION |
| 80 이상 95 미만 | DEGRADING |
| 95 이상 | WARNING |

추가 검증 기준:

```text
Validation 95백분위수 → anomaly threshold
Validation 99백분위수 → strong anomaly threshold
```

임계값은 Test 결과를 본 뒤 유리하게 변경하지 않는다. 변경이 필요하면 새 `model_run_id`로 다시 실행한다.

---

## 10. 결과 생성

실행 명령:

```bash
.venv/bin/python scripts/score_anomalies.py \
  --input data/processed/sensor_features_1min.parquet \
  --run-summary data/processed/anomaly/run_summary.json \
  --output data/processed/anomaly/scored_windows.parquet
```

필수 출력 컬럼:

```text
model_run_id
window_start
sensor_id
dataset_split
sigma_anomaly
sigma_feature_count
sigma_detected_features
isolation_decision
isolation_severity
risk_score
status
```

---

## 11. 라벨 없는 평가

다음 결과를 생성한다.

```text
docs/part2/results/split_summary.csv
docs/part2/results/model_run_summary.json
docs/part2/results/sensor_metrics.csv
docs/part2/results/method_overlap.csv
docs/part2/results/monthly_anomaly_rate.csv
docs/part2/results/data_quality_effect.csv
docs/part2/results/top_anomaly_windows.csv
```

평가 실행 명령:

```bash
.venv/bin/python scripts/evaluate_anomalies.py \
  --input data/processed/anomaly/scored_windows.parquet \
  --run-summary data/processed/anomaly/run_summary.json \
  --results-dir docs/part2/results \
  --images-dir docs/part2/images \
  --top-windows 50
```

확인 항목:

- [x] 센서별 Test 이상 비율이 기록됐다.
- [x] 3-Sigma와 Isolation Forest 교집합이 기록됐다.
- [x] 월별 이상 비율의 변화를 검토했다.
- [x] Risk Score 상위 50개 Window 그래프를 검토했다.
- [x] 낮은 `sample_count`의 영향을 확인했다.
- [x] 데이터 공백 직후 Window의 영향을 확인했다.

센서 92는 낮은 표본 Window와 60분 이상 공백 직후의 이상률이 상대적으로 높아 품질 주의가 필요했다. 센서 84의 높은 Test 이상률은 이 두 조건으로 설명되지 않았다.

---

## 12. 데이터베이스 구조

`sql/003_anomaly_schema.sql`에서 다음 테이블을 생성한다.

```text
anomaly_model_run
├─ 실행 ID, Feature와 모델 파라미터
└─ Risk Score 임계값

anomaly_model_sensor
├─ 센서별 학습·검증·Test 기간
└─ 3-Sigma Profile과 Validation 임계값

anomaly_result
├─ Window별 통계·ML 이상 결과
├─ Risk Score와 상태
└─ model_run_id 참조
```

권장 키:

```text
anomaly_model_run: model_run_id
anomaly_model_sensor: model_run_id + sensor_id
anomaly_result: sensor_id + window_start + model_run_id
```

---

## 13. DB 적재와 검증

스키마 적용:

```bash
docker compose exec -T timescaledb \
  psql -v ON_ERROR_STOP=1 -U smart_factory -d smart_factory \
  < sql/003_anomaly_schema.sql
```

적재 명령:

```bash
set -a
source .env
set +a

.venv/bin/python scripts/load_anomaly_results.py \
  --run-summary data/processed/anomaly/run_summary.json \
  --input data/processed/anomaly/scored_windows.parquet
```

검증 SQL:

```sql
SELECT
    model_run_id,
    sensor_id,
    count(*) AS window_count,
    count(*) FILTER (WHERE status = 'WARNING') AS warning_count,
    min(window_start) AS first_window,
    max(window_start) AS last_window
FROM anomaly_result
GROUP BY model_run_id, sensor_id
ORDER BY model_run_id, sensor_id;
```

### 적재 결과

| 항목 | 결과 |
|---|---|
| Model Run ID | `iforest_2019_02_2019_07_v1` |
| 모델 센서 | 92, 109, 84 |
| 결과 행 수 | 387,741 |
| Test 행 수 | 77,549 |
| Test 3-Sigma 이상 | 5,134 |
| Test Isolation 이상 | 5,852 |
| Test 공통 이상 | 3,516 |
| 전체 WARNING 행 수 | 17,366 |
| 적재 실패 행 수 | 0 |
| `anomaly_result` | 168MB / 7개 Chunk |
| DB 전체 크기 | 8,439MB |

---

## 14. 완료 체크리스트

### 데이터와 모델

- [x] 시간 순서 Split이 완료됨
- [x] 3-Sigma Baseline이 생성됨
- [x] 센서별 Isolation Forest가 학습됨
- [x] Validation 기반 Risk Score가 고정됨
- [x] Test 결과가 생성됨

### 검증

- [x] 모델 간 탐지 결과를 비교함
- [x] 상위 이상 구간을 시각 검토함
- [x] 월별 탐지 안정성을 확인함
- [x] 데이터 품질 영향과 공정 상태 변화 후보를 구분해 기록함

### 저장

- [x] 모델 실행 메타데이터를 저장함
- [x] `anomaly_result`를 TimescaleDB에 적재함
- [x] Parquet과 DB 행 수가 일치함
- [x] Part 2 블로그의 결과 표와 그래프를 채움

---

## 15. Part 3 진행 조건

다음 조건을 만족한 뒤 MCP Tool 구현으로 진행한다.

```text
동일 입력에서 결과 재현 가능
Risk Score 방향과 범위가 일관됨
센서별 최신 상태 SQL 조회 가능
상위 이상 구간과 근거 Feature 조회 가능
```
