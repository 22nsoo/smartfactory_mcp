# 스마트 팩토리 SCADA 이상탐지 + MCP + RAG 시스템 실행 방안

## 현재 구현 상태 — 2026-08-09

```text
Part 1  6개월 EDA + TimescaleDB 적재             완료
Part 2  3-Sigma + Isolation Forest + Risk Score  완료
Part 3  Flask API + MCP 조회 Tool                완료
Part 4  RAG + LangChain + LangGraph + ChromaDB   오프라인 MVP 완료
```

Part 2 결과는 센서 `92`, `109`, `84`의 387,741개 1분 Window를 대상으로 생성했으며, `anomaly_result`에 전량 적재했다. 상세 결과와 재현 명령은 `docs/part2/`에 기록한다.

Part 3에서는 이 결과를 조회하는 Flask 대시보드·REST API와 읽기 전용 MCP 2.0 Tool 7개를 구현했다. Flask HTTP 테스트와 MCP stdio Client 종단 간 테스트를 완료했으며 상세 실행 방법은 `docs/part3/`에 기록한다.

Part 4에서는 프로젝트용 일반 점검 문서 4개를 20개 Chunk로 나눠 ChromaDB에 저장하고, LangChain Retriever와 LangGraph의 `sensor`, `knowledge`, `hybrid` 경로를 구현했다. Flask `/api/ask`까지 연결했으며 외부 LLM 대신 결정론적 오프라인 Template으로 답변한다.

## 1. 프로젝트 개요

### 1.1 프로젝트 목표
자동차 용접 생산라인의 SCADA 센서 데이터를 기반으로 설비 상태를 분석하고, 머신러닝 기반 이상탐지 결과와 정비 문서를 연계하여 사용자가 자연어로 설비 상태·이상 원인·점검 방법을 질의할 수 있는 스마트 팩토리 AI 시스템을 구축한다.

최종 시스템은 다음 기능을 제공하는 것을 목표로 한다.

- SCADA 센서 데이터 저장 및 시계열 조회
- 센서별 정상 범위 및 통계 분석
- 머신러닝 기반 이상 탐지
- 이상 센서 및 이상 구간 자동 식별
- 센서 상태와 이상 점수 조회
- 정비 매뉴얼·SOP·고장 사례 기반 RAG 검색
- MCP를 통한 데이터 조회 및 ML 기능의 Tool 표준화
- LangGraph 기반 질의 라우팅 및 Agent Workflow 구성
- LLM을 통한 자연어 기반 설비 상태 설명 및 점검 가이드 제공

---

## 2. 활용 데이터

### 2.1 데이터셋
**I-BiDaaS CRF SCADA Dataset**

- 대상: 차량 서브어셈블리 용접 생산라인
- 원본 배포 형식: 3.91GB 7z 압축 파일
- 압축 해제 크기: 약 31GB(28.9GiB)
- 파일 구성: `SCADA/YYYY_MM/*.csv` 형식의 일별 CSV 868개
- 파일 상태: 데이터 포함 659개, 헤더만 존재하는 파일 209개
- 구분자: 세미콜론(`;`) 866개, 쉼표(`,`) 2개
- 기본 컬럼
  - `id`: 센서 식별자
  - `value`: 센서 측정값
  - `unit`: 측정 단위
  - `timestamp`: 측정 시간
- 메타데이터 기준 센서 수: 147개

실제 CSV에는 `0,445`, `117.383`, `3.796.795`처럼 숫자 표기가 혼재하고 일부 파일에는 불필요한 빈 컬럼이 존재한다. 따라서 일반적인 단일 `read_csv()` 호출이 아니라 파일별 구분자 감지와 값 정규화가 필요하다. 선택한 6개월 EDA에서는 실제 센서 149개와 184,664,873행을 확인했다.

### 2.2 센서 종류

| 센서 종류 | 센서 수 | 단위 |
|---|---:|---|
| 가속도 | 87 | mg |
| 속도 | 30 | mm/s |
| 온도 | 9 | °C |
| 압력 | 8 | bar |
| 유량 | 8 | l/min |
| 변위 | 1 | mm |
| 기타 에너지 관련 센서 | 4 | l, m³ 등 |

### 2.3 데이터 활용 방향

- 가속도/속도 → 진동 상태 및 기계 이상 감지
- 온도 → 과열 및 비정상 온도 변화 감지
- 압력 → 공정 압력 이상 탐지
- 유량 → 유량 변화 및 이상 상태 탐지
- 시간 변화 → 열화 추세 및 예지보전 후보 탐색

> 주의: 제공 메타데이터에는 센서 ID와 실제 개별 장비의 상세 매핑 및 명확한 고장 라벨이 포함되어 있지 않으므로, 초기 프로젝트에서는 **비지도 이상탐지와 상태 모니터링**을 중심으로 구현한다.

---

# 3. 전체 시스템 아키텍처

```text
                         User
                          │
                          ▼
                     LangGraph
                  Agent Workflow
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
         MCP Server              RAG Retriever
              │                       │
       ┌──────┴───────┐               ▼
       │              │            ChromaDB
       ▼              ▼
PostgreSQL/       ML Inference
TimescaleDB       Isolation Forest
       │              │
       └──────┬───────┘
              │
              └───────────┬───────────┘
                          ▼
                         LLM
                          │
                          ▼
                     최종 답변
```

---

# 4. 기술 스택

| 영역 | 기술 | 역할 |
|---|---|---|
| 데이터 처리 | Python, Pandas, NumPy | 대용량 CSV 전처리 및 EDA |
| 시각화 | Matplotlib | 센서 분포 및 시계열 분석 |
| 시계열 DB | PostgreSQL + TimescaleDB | 센서 데이터 및 이상탐지 결과 저장 |
| 머신러닝 | Scikit-learn | Isolation Forest 기반 이상탐지 |
| Vector DB | ChromaDB | 정비 매뉴얼 및 SOP 임베딩 저장 |
| RAG | LangChain | 문서 검색 및 Retriever 구성 |
| Agent | LangGraph | 질의 분류 및 실행 Workflow |
| Tool 표준화 | MCP / FastMCP | DB 조회 및 ML 분석 Tool 제공 |
| LLM 연동 | LangChain | LLM, Tool, Retriever 연결 |
| UI 선택사항 | Streamlit | 모니터링 및 데모 UI |

---

# 5. 단계별 실행 방안

## Phase 1. 데이터 구조 확인 및 EDA

### 목적
압축 해제 후 약 31GB인 868개 CSV를 한 번에 메모리에 적재하지 않고 파일별·chunk 단위로 읽는다. 1차 순회에서 전체 통계와 데이터 품질을 계산하고, 2차 순회에서 이상치와 대표 샘플을 수집한다.

### 주요 작업

1. 전체 CSV 목록, 용량, 헤더 전용 파일 및 파일별 오류 확인
2. 파일 헤더를 이용한 세미콜론/쉼표 구분자 자동 감지
3. 컬럼명을 소문자로 통일하고 필요한 4개 컬럼만 로딩
4. 혼합 소수점 표기와 손상된 온도 단위 정규화
5. 전체 행 수, 센서 수, 센서별 레코드 수 및 단위 확인
6. 결측값과 숫자·Timestamp 변환 실패 건수 집계
7. Chunk 통계를 병합해 센서별 최소/최대/평균/표준편차 계산
8. 전체 데이터 수집 기간 확인
9. 3-Sigma 기준 전체 이상치 개수와 비율 계산
10. 상위 센서의 기간 전체 대표 샘플 수집
11. 센서별 샘플링 주기 및 분포·시계열 시각화

### 산출물

```text
eda_output/
├── dataset_summary.json
├── file_inventory.csv
├── data_quality.csv
├── sensor_summary.csv
├── unit_distribution.csv
├── missing_values.csv
├── outlier_summary.csv
├── sampling_interval.csv
├── sensor_xxx_histogram.png
├── sensor_xxx_timeseries.png
├── sensor_boxplot.png
├── sensor_record_counts.png
└── sensor_unit_distribution.png
```

### 완료 기준

- 실제 센서 개수가 확인됨
- 868개 파일의 처리 성공·실패 여부가 기록됨
- 숫자 및 Timestamp 변환 실패 비율이 확인됨
- 센서별 단위가 식별됨
- 센서별 데이터 수집 주기가 파악됨
- 이상치 및 분포 특성이 확인됨
- ML 입력에 사용할 센서 후보가 선정됨

### 현재 구현 상태

- `eda.py`에 다중 CSV, 혼합 구분자, 혼합 숫자 형식 및 2-pass 처리를 구현함
- 기본 분석 범위를 데이터 가용률이 가장 높은 연속 6개월인 `2019_02`~`2019_07`로 설정함
- 기본 범위는 CSV 180개, 데이터 포함 파일 157개, 약 6.69GiB임
- 첫 번째 CSV 1개, 1,048,575행 스모크 테스트를 완료함
- 스모크 테스트에서 센서 33개를 확인했으며 숫자·Timestamp 변환 실패와 파일 처리 실패는 없었음
- 쉼표 구분 예외 파일의 파싱을 별도로 검증함
- 전체 868개 파일 EDA 실행 및 최종 센서 후보 선정은 아직 진행 전임

6개월 MVP EDA 실행 명령:

```bash
.venv/bin/python eda.py
```

전체 기간을 분석해야 할 경우 명시적으로 범위를 지정한다.

```bash
.venv/bin/python eda.py \
  --start-month 2018_04 \
  --end-month 2020_09 \
  --output-dir eda_output_full
```

---

# 6. Phase 2. 데이터 전처리

## 6.1 데이터 타입 정리

```python
df.columns = df.columns.str.strip().str.lower()

value = df["value"].astype("string").str.strip()
value = value.str.replace(",", ".", regex=False)
multiple_dots = value.str.count(r"\.").gt(1).fillna(False)
value.loc[multiple_dots] = value.loc[multiple_dots].str.replace(
    r"\.(?=.*\.)", "", regex=True
)
df["value"] = pd.to_numeric(value, errors="coerce")

df["timestamp"] = pd.to_datetime(
    df["timestamp"],
    format="%Y-%m-%d %H:%M:%S.%f",
    errors="coerce"
)
```

`3.796.795`는 현재 정규화 규칙에 따라 `3796.795`로 처리한다. 이 규칙은 `data_quality.csv`의 `multiple_dot_value` 건수와 센서별 분포를 이용해 전체 EDA 후 다시 검증한다.

## 6.2 센서 종류 구분

단위를 기준으로 센서를 그룹화한다.

```text
mg     → Acceleration
mm/s   → Velocity
°C     → Temperature
bar    → Pressure
l/min  → Flow
```

## 6.3 결측치 처리

- `timestamp` 누락 또는 변환 실패 → 분석 대상에서 제외하고 건수 기록
- `value` 누락 또는 변환 실패 → 분석 대상에서 제외하고 건수 기록
- 센서 데이터의 장시간 누락 → Data Quality Warning 후보

## 6.4 이상 데이터 검토

다음 값을 별도로 확인한다.

- 비정상적으로 큰 값
- 비정상적으로 작은 값
- 동일한 값이 지나치게 오래 유지되는 경우
- 시간 역순
- 중복 Timestamp
- 비정상 Sampling Gap

---

# 7. Phase 3. 시계열 데이터 저장

## 7.1 PostgreSQL + TimescaleDB 사용 목적

대용량 SCADA 데이터를 CSV에서 매번 검색하지 않고 데이터베이스에서 빠르게 조회한다.

### 기본 테이블

```sql
CREATE TABLE sensor_data (
    timestamp TIMESTAMPTZ NOT NULL,
    sensor_id INTEGER NOT NULL,
    value DOUBLE PRECISION,
    unit VARCHAR(20)
);
```

TimescaleDB 적용 후 시계열 테이블로 구성한다.

### 이상탐지 결과 테이블

```sql
anomaly_model_run     -- 전체 실행 파라미터와 버전
anomaly_model_sensor  -- 센서별 Split, 3-Sigma, Validation 임계값
anomaly_result        -- Window별 점수, Risk Score, 상태
```

실제 DDL은 `sql/003_anomaly_schema.sql`에 관리한다.

### 센서 통계 테이블

```sql
CREATE TABLE sensor_profile (
    sensor_id INTEGER PRIMARY KEY,
    unit VARCHAR(20),
    mean DOUBLE PRECISION,
    std DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    lower_threshold DOUBLE PRECISION,
    upper_threshold DOUBLE PRECISION
);
```

---

# 8. Phase 4. Feature Engineering

Raw 센서 값만 ML에 입력하지 않고 일정 시간 Window 단위로 특징을 생성한다.

## 8.1 기본 Feature

```text
mean
std
min
max
median
RMS
peak-to-peak
slope
```

## 8.2 진동 데이터 추천 Feature

가속도 및 속도 센서의 경우 우선 다음 Feature를 사용한다.

- Mean
- Standard Deviation
- RMS
- Maximum
- Minimum
- Peak-to-Peak
- Trend Slope

추후 데이터 샘플링 주기가 충분히 빠르다면 주파수 영역 Feature 추가를 검토한다.

```text
FFT Peak Frequency
Spectral Energy
Frequency Band Energy
```

## 8.3 Window 크기

EDA에서 확인한 Sampling Interval에 따라 결정한다.

예:

```text
샘플링 주기 1초
→ 60개 = 1분 Window

샘플링 주기 10초
→ 6개 = 1분 Window
```

Window 크기를 임의로 정하지 않고 실제 센서 수집 주기를 기준으로 결정한다.

---

# 9. Phase 5. 머신러닝 이상탐지

## 9.1 Baseline

Train 60% 구간으로 통계 기반 이상탐지를 구현했다.

```text
Lower = Mean - 3 × STD
Upper = Mean + 3 × STD
```

이를 ML 결과와 비교하기 위한 Baseline으로 사용한다.

---

## 9.2 Isolation Forest

### 선정 이유

현재 데이터에는 명확한 고장 Label이 없으므로 지도학습보다 비지도 이상탐지가 적합하다.

### 입력

```text
Mean
STD
RMS
Peak-to-Peak
Slope
...
```

### 출력

```text
Prediction:
 1  → Normal
-1  → Anomaly

Anomaly Score:
연속적인 이상 정도
```

### 실제 구현

```python
from sklearn.ensemble import IsolationForest

model = IsolationForest(
    n_estimators=300,
    contamination="auto",
    random_state=42,
    n_jobs=-1,
)

model.fit(X_train)

severity = -model.decision_function(X)
```

센서별 시간 순서로 Train/Validation/Test를 60/20/20 분리했다. Validation severity의 경험적 백분위수를 0~100 Risk Score로 변환하고 95점 이상을 WARNING으로 기록한다. Test에서는 77,549개 Window 중 Isolation Forest 이상 5,852개, 3-Sigma 이상 5,134개, 공통 이상 3,516개가 확인됐다.

---

# 10. Phase 6. 이상 상태 정의

머신러닝 결과를 그대로 사용자에게 보여주지 않고 운영 상태로 변환한다.

예시:

```text
NORMAL
ATTENTION
DEGRADING
WARNING
```

초기 기준:

```text
NORMAL
- 이상 점수 정상
- 최근 추세 변화 없음

ATTENTION
- 단발성 이상 발생

DEGRADING
- 이상 점수 증가
- RMS 또는 STD 지속 상승

WARNING
- 강한 이상점수
- 연속 이상구간 발생
```

단, 실제 임계값은 EDA 및 실험 결과를 통해 결정한다.

---

# 11. Phase 7. Health Score

센서 상태를 사용자가 이해하기 쉽게 점수화한다.

예:

```text
Health Score = 0 ~ 100
```

초기 설계 예:

```text
80 ~ 100 → NORMAL
60 ~ 79  → ATTENTION
40 ~ 59  → DEGRADING
0  ~ 39  → WARNING
```

Health Score는 다음 요소를 조합할 수 있다.

```text
Anomaly Score
+ Trend
+ 최근 이상 발생 빈도
+ RMS 변화율
+ 현재 Threshold 초과 정도
```

고장 Label이 없는 상태에서는 이를 실제 RUL로 표현하지 않고 **설비 상태 지수**로 정의한다.

---

# 12. Phase 8. 머신러닝 결과 검증

고장 Ground Truth가 없는 경우 Accuracy만 사용하는 것은 적절하지 않다.

다음 기준으로 평가한다.

### 12.1 이상 구간 시각 검증

```text
Sensor Value
+
Rolling Mean
+
3-Sigma Threshold
+
Isolation Forest Anomaly
```

를 한 그래프에 표시한다.

### 12.2 모델 비교

```text
3-Sigma
vs
Isolation Forest
```

비교 항목:

- 이상 검출 개수
- 이상 구간 지속 시간
- 단발성 Spike 여부
- Trend 변화 탐지 여부

### 12.3 후속 모델 비교

시간이 허용될 경우:

```text
Isolation Forest
vs
Autoencoder
```

또는

```text
Isolation Forest
vs
One-Class SVM
```

을 비교한다.

---

# 13. Phase 9. MCP Server 구축

## 13.1 목적

DB 조회와 ML 기능을 LLM에서 직접 구현하지 않고 MCP Tool 형태로 표준화한다.

## 13.2 주요 MCP Tool

### 설비 상태 조회

```python
@mcp.tool()
def get_sensor_status(sensor_id: int):
    ...
```

반환 예:

```json
{
  "sensor_id": 667,
  "unit": "mg",
  "latest_value": 52.7,
  "anomaly_score": 0.81,
  "status": "WARNING"
}
```

### 센서 이력

```python
@mcp.tool()
def get_sensor_history(
    sensor_id: int,
    hours: int = 1
):
    ...
```

### 이상 센서 목록

```python
@mcp.tool()
def get_abnormal_sensors():
    ...
```

### 상태 점수

```python
@mcp.tool()
def get_health_score(sensor_id: int):
    ...
```

### 센서 Feature

```python
@mcp.tool()
def get_sensor_features(sensor_id: int):
    ...
```

### 전체 상태 요약

```python
@mcp.tool()
def get_factory_summary():
    ...
```

---

# 14. Phase 10. RAG 구축

## 14.1 목적

SCADA 센서값 자체는 TimescaleDB에서 조회하고, 설비 점검 방법과 정비 지식은 RAG에서 검색한다.

## 14.2 ChromaDB 저장 대상

다음과 같은 문서를 사용한다.

- 설비 매뉴얼
- 센서 매뉴얼
- 예방정비 가이드
- SOP
- 고장 사례 문서
- 안전 점검 문서
- 산업용 로봇 정비 문서

## 14.3 RAG 처리 과정

```text
정비 문서
   ↓
Text Splitter
   ↓
Embedding
   ↓
ChromaDB
   ↓
Similarity Search
   ↓
관련 문서 Chunk
   ↓
LLM Context
```

## 14.4 Metadata 예

```json
{
  "equipment": "welding_robot",
  "category": "maintenance",
  "document": "robot_maintenance_manual",
  "section": "vibration"
}
```

가능한 경우 Metadata Filter를 이용해 검색 범위를 제한한다.

---

# 15. Phase 11. LangChain 구성

LangChain은 다음 구성요소 연결에 사용한다.

- LLM
- MCP Tool
- ChromaDB Retriever
- Prompt
- Embedding Model
- Structured Output

역할은 **각 AI 컴포넌트의 연결 계층**으로 정의한다.

---

# 16. Phase 12. LangGraph Agent Workflow

사용자 질문에 따라 필요한 기능만 실행하도록 Workflow를 구성한다.

## 16.1 질문 유형

### Type A. 센서 조회

```text
"667번 센서 현재 값 알려줘"
```

흐름:

```text
User
→ LangGraph
→ MCP get_sensor_status()
→ TimescaleDB
→ LLM
```

### Type B. 이상 센서 탐색

```text
"현재 이상 센서 알려줘"
```

흐름:

```text
User
→ LangGraph
→ MCP get_abnormal_sensors()
→ anomaly_result
→ LLM
```

### Type C. 이상 원인 분석

```text
"667번 센서가 왜 이상해?"
```

흐름:

```text
User
→ LangGraph
→ get_sensor_features()
→ get_sensor_history()
→ get_health_score()
→ LLM
```

### Type D. 점검 방법

```text
"이런 진동 이상이 발생하면 무엇을 확인해야 해?"
```

흐름:

```text
User
→ LangGraph
├─ MCP → 현재 센서 상태
└─ RAG → 관련 정비 문서
        ↓
       LLM
```

---

# 17. Phase 13. 최종 응답 생성

LLM은 다음 정보를 조합한다.

```text
현재 센서값
+
최근 센서 이력
+
ML 이상점수
+
상태 등급
+
Feature 변화
+
RAG 검색 문서
```

예시:

```text
667번 센서는 최근 30분 동안 진동 RMS와 표준편차가 상승했으며,
Isolation Forest 이상점수 역시 WARNING 기준을 초과했습니다.

현재 패턴은 정상 구간과 비교해 변동성이 증가한 상태입니다.

관련 정비 문서에서는 지속적인 진동 상승 발생 시
체결 상태 및 회전체 상태를 우선 점검하도록 안내하고 있습니다.
```

LLM이 데이터에 없는 고장 원인을 확정적으로 생성하지 않도록 Prompt에 제한 조건을 포함한다.

---

# 18. Phase 14. Streamlit 대시보드

선택사항이지만 데모 완성도를 위해 구현을 권장한다.

## 주요 화면

### 전체 현황

```text
전체 센서: 147
NORMAL: xx
ATTENTION: xx
DEGRADING: xx
WARNING: xx
```

### 센서 상세

```text
Sensor ID
현재값
Unit
Health Score
Anomaly Score
현재 상태
```

### 차트

- Raw Value
- Rolling Mean
- Normal Threshold
- Anomaly 구간
- RMS
- Anomaly Score

### AI Assistant

사용자가 대시보드에서 자연어로 질문할 수 있도록 구성한다.

---

# 19. 디렉터리 구조 예시

```text
smart_factory_mcp/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── documents/
│
├── notebooks/
│   ├── 01_eda.ipynb
│   ├── 02_preprocessing.ipynb
│   └── 03_ml_experiment.ipynb
│
├── src/
│   ├── preprocessing/
│   │   ├── loader.py
│   │   ├── cleaner.py
│   │   └── feature_engineering.py
│   │
│   ├── ml/
│   │   ├── train.py
│   │   ├── inference.py
│   │   └── health_score.py
│   │
│   ├── database/
│   │   ├── postgres.py
│   │   └── queries.py
│   │
│   ├── rag/
│   │   ├── ingest.py
│   │   ├── retriever.py
│   │   └── chroma.py
│   │
│   ├── mcp/
│   │   └── server.py
│   │
│   ├── agent/
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   └── prompts.py
│   │
│   └── app/
│       └── streamlit_app.py
│
├── models/
│   └── isolation_forest.pkl
│
├── tests/
│   ├── test_mcp.py
│   ├── test_db.py
│   └── test_agent.py
│
├── requirements.txt
├── docker-compose.yml
├── README.md
└── .env
```

---

# 20. 구현 우선순위

처음부터 모든 기술을 동시에 연결하지 않는다.

## 1차 목표 — 데이터 분석

```text
CSV
↓
EDA
↓
전처리
↓
Feature Engineering
```

## 2차 목표 — ML

```text
Feature
↓
3-Sigma Baseline
↓
Isolation Forest
↓
Anomaly Score
```

## 3차 목표 — DB

```text
Processed Sensor Data
↓
PostgreSQL / TimescaleDB
↓
Anomaly Result 저장
```

## 4차 목표 — MCP

```text
TimescaleDB
+
ML Result
↓
MCP Tools
```

## 5차 목표 — RAG

```text
Manual / SOP
↓
Embedding
↓
ChromaDB
↓
Retriever
```

## 6차 목표 — Agent

```text
LangChain
+
LangGraph
+
MCP
+
RAG
↓
자연어 질의 시스템
```

## 7차 목표 — UI

```text
Streamlit Dashboard
+
AI Assistant
```

---

# 21. 권장 개발 일정

## Week 1 — EDA 및 전처리

- 대용량 CSV Chunk Loading
- 센서 구조 파악
- 센서별 통계
- Sampling Interval 분석
- 시계열 그래프
- 이상치 탐색
- 사용할 센서 후보 선정

### 결과물
`EDA Report + sensor_summary.csv`

---

## Week 2 — ML 이상탐지

- Window 정의
- Feature Engineering
- 3-Sigma Baseline
- Isolation Forest 학습
- Anomaly Score 생성
- 결과 시각화
- Health Score 초기 정의

### 결과물
`Isolation Forest Model + Anomaly Result`

---

## Week 3 — PostgreSQL / TimescaleDB

- DB 구축
- 센서 데이터 적재
- anomaly_result 테이블 생성
- 주요 SQL Query 작성
- Python DB 연동

### 결과물
`SCADA Time-Series DB`

---

## Week 4 — MCP

- FastMCP Server 구축
- 센서 조회 Tool
- 이상 센서 Tool
- Feature 조회 Tool
- Health Score Tool
- MCP Client 테스트

### 결과물
`Smart Factory MCP Server`

---

## Week 5 — RAG

- 정비 문서 수집
- 문서 전처리
- Chunking
- Embedding
- ChromaDB 구축
- Retriever 검증

### 결과물
`Maintenance Knowledge RAG`

---

## Week 6 — LangGraph Agent

- 질문 분류
- MCP Routing
- RAG Routing
- MCP + RAG 병렬 호출
- LLM 최종 답변 생성
- Hallucination 제어 Prompt

### 결과물
`Smart Factory AI Agent`

---

## Week 7 — 통합 및 UI

- Streamlit Dashboard
- 자연어 질의 UI
- 전체 시스템 Integration Test
- Demo Scenario 작성
- 발표 자료 제작

### 결과물
`최종 Demo`

---

# 22. 최종 데모 시나리오

## Scenario 1 — 현재 상태 조회

**사용자**

```text
현재 이상 센서 알려줘.
```

**System**

```text
MCP
→ DB 조회
→ WARNING 센서 반환
→ LLM 설명
```

---

## Scenario 2 — 특정 센서 분석

**사용자**

```text
667번 센서가 왜 이상으로 판단됐어?
```

**System**

```text
센서 History
+
RMS
+
STD
+
Slope
+
Anomaly Score
↓
LLM
```

---

## Scenario 3 — 추세 분석

**사용자**

```text
최근 상태가 계속 악화되는 센서가 있어?
```

**System**

```text
Trend Analysis
+
Anomaly History
↓
DEGRADING 센서 탐색
```

---

## Scenario 4 — 정비 지식 결합

**사용자**

```text
667번과 비슷한 진동 이상은 어떻게 점검해야 해?
```

**System**

```text
MCP
→ 현재 센서 상태

RAG
→ 관련 정비 문서

두 결과 결합
→ LLM 답변
```

---

# 23. 프로젝트 평가 지표

## 데이터 처리

- 전체 데이터 처리 성공 여부
- Chunk Loading 안정성
- DB 적재 성능
- 시계열 Query 응답 시간

## 머신러닝

- 이상 구간 탐지 결과
- 이상 점수 안정성
- Baseline 대비 탐지 특성
- 오탐 분석
- 센서별 이상 비율

## RAG

- 관련 문서 검색 정확도
- Top-k Retrieval 결과
- 답변 근거 문서 일치 여부

## MCP / Agent

- Tool 호출 성공률
- 올바른 Tool Routing 여부
- MCP 응답 시간
- 질문 유형별 Workflow 성공률

## LLM

- 센서 데이터와 답변 일치 여부
- 근거 없는 고장 원인 생성 여부
- RAG 근거 활용 여부

---

# 24. 프로젝트 차별화 포인트

단순히 LLM에 데이터를 입력해 답변을 생성하는 프로젝트가 아니라 다음 구조를 갖는 것이 핵심이다.

### 1. 실제 자동차 생산라인 SCADA 데이터
자동차 용접 생산라인 센서 데이터를 활용한다.

### 2. 시계열 데이터베이스
대규모 센서 데이터를 PostgreSQL/TimescaleDB로 관리한다.

### 3. 머신러닝 이상탐지
Isolation Forest 기반 비지도 이상탐지를 수행한다.

### 4. MCP
센서 조회와 ML 분석 기능을 MCP Tool로 표준화한다.

### 5. RAG
정비 문서 및 SOP에서 관련 대응 정보를 검색한다.

### 6. Agent Workflow
LangGraph가 질문 목적을 분석하여 DB, ML, RAG 중 필요한 기능을 선택한다.

### 7. 설명 가능한 자연어 인터페이스
사용자가 SQL, 센서 ID 구조, ML 모델 구조를 몰라도 자연어로 설비 상태를 조회할 수 있도록 한다.

---

# 25. 최종 프로젝트 한 문장 정의

> 자동차 용접 생산라인의 SCADA 센서 데이터를 PostgreSQL/TimescaleDB로 관리하고, Isolation Forest를 활용해 설비 이상 상태를 탐지하며, 센서 조회 및 ML 분석 기능을 MCP Tool로 표준화하고, ChromaDB 기반 RAG와 LangGraph Agent를 결합하여 자연어로 설비 상태·이상 원인·점검 방법을 조회할 수 있는 스마트 팩토리 AI 시스템을 구축한다.

---

# 26. 구현 시 주의사항

1. 센서 ID와 실제 장비 정보가 확인되지 않으면 특정 장비 고장을 단정하지 않는다.
2. 고장 Label이 없으므로 모델 결과를 RUL 또는 고장 확률로 표현하지 않는다.
3. `Anomaly Score`, `Health Score`, `DEGRADING` 등의 용어는 프로젝트 내부 정의임을 명시한다.
4. 정비 방법은 가능한 경우 RAG 검색 문서의 근거를 함께 제공한다.
5. LLM이 데이터에 없는 원인을 생성하지 않도록 시스템 Prompt를 구성한다.
6. 원본은 3.91GB 압축 파일이며 압축 해제 후 약 31GB이므로, EDA 이후 정제된 Parquet 또는 TimescaleDB를 사용하고 원본 CSV를 서비스 요청마다 직접 조회하지 않는다.
7. EDA 결과를 바탕으로 Window Size, Threshold, contamination 값을 결정한다.
8. 처음부터 LangGraph/RAG를 붙이지 않고 데이터 → ML → DB → MCP → RAG → Agent 순으로 단계적으로 통합한다.

---

# 27. 참고 데이터셋

**I-BiDaaS - CRF - SCADA Dataset**

- DOI: `10.5281/zenodo.4265324`
- 차량 서브어셈블리 용접 생산라인의 SCADA 센서 데이터
- 센서 데이터를 활용한 이상 측정값 Threshold 산출 및 Predictive Maintenance 분석 목적
