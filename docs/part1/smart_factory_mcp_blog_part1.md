> 이 글은 스마트 팩토리 SCADA 이상탐지 + MCP 프로젝트의 Part 1이다.  
> 이번 편에서는 원본 데이터 구조를 파악하고, 6개월 분석 범위를 정한 뒤, 대용량 EDA와 TimescaleDB 적재 구조를 설계한다.

## 들어가며

이번 프로젝트의 최종 목표는 자동차 생산라인의 센서 상태를 자연어로 조회할 수 있는 시스템을 만드는 것이다.

사용자가 다음과 같이 질문하는 모습을 생각했다.

```text
현재 이상 징후가 있는 센서는 무엇인가요?
특정 진동 센서의 최근 상태는 어떤가요?
이런 진동 패턴에서는 무엇을 점검해야 하나요?
```

최종적으로는 다음 기술이 연결될 예정이다.

```text
SCADA 데이터
→ 이상탐지 모델
→ PostgreSQL / TimescaleDB
→ MCP Tool
→ RAG
→ LangGraph Agent
→ 자연어 답변
```

하지만 LLM이나 MCP부터 시작할 수는 없다. 센서 데이터가 어떤 구조인지, 값이 정상적으로 읽히는지, 어떤 센서를 모델링할 수 있는지부터 확인해야 한다.

Part 1에서는 가장 앞단인 데이터 분석과 저장 구조에 집중한다.

---

## 1. 사용할 데이터셋

사용한 데이터는 **I-BiDaaS CRF SCADA Dataset**이다.

- 자동차 서브어셈블리 용접 생산라인의 센서 데이터
- 메타데이터 기준 센서 147개
- 가속도, 속도, 온도, 압력, 유량 등의 측정값 포함
- 센서 ID는 익명화
- 명확한 정상/고장 라벨 없음
- DOI: [`10.5281/zenodo.4265324`](https://doi.org/10.5281/zenodo.4265324)

고장 라벨이 없기 때문에 초기 모델은 지도학습보다 3-Sigma와 Isolation Forest 같은 비지도 이상탐지를 사용하는 방향으로 정했다.

### 실제 파일을 열어보고 알게 된 것

배포 페이지에는 약 3.9GB 파일로 표시되지만, 이것은 압축 파일의 크기다.

```text
7z 압축 파일             약 3.91GB
압축 해제 후             약 31GB (28.9GiB)
일별 CSV                 868개
데이터가 있는 CSV        659개
헤더만 있는 CSV          209개
기간                     2018-04 ~ 2020-09
```

단순히 큰 데이터만의 문제도 아니었다. 파일 형식도 완전히 일관적이지 않았다.

```text
세미콜론 구분 파일       866개
쉼표 구분 파일           2개
기본 컬럼                id, value, unit, timestamp
```

일부 값은 다음처럼 서로 다른 형식으로 저장되어 있었다.

```text
0,445
117.383
3.796.795
```

온도 단위도 일부 파일에서 `°C`가 아니라 `�C`로 손상되어 있었다.

---

## 2. 처음 발생한 오류

처음 작성한 코드는 7z 파일을 곧바로 `pandas.read_csv()`에 전달했다.

```python
sample = pd.read_csv(FILE_PATH, nrows=20)
```

실행 결과는 다음과 같았다.

```text
UnicodeDecodeError: 'utf-8' codec can't decode byte ...
```

처음에는 인코딩 문제처럼 보였지만, 실제 원인은 pandas가 7z 압축 데이터를 CSV 텍스트로 읽으려 했기 때문이었다.

압축을 해제한 뒤에도 두 가지 문제가 남았다.

1. 대부분은 세미콜론 구분이지만 2개 파일은 쉼표 구분이다.
2. 실제 컬럼은 `Id`, `Value`가 아니라 소문자 `id`, `value`다.

여기서 얻은 첫 번째 교훈은 명확했다.

> 대용량 데이터에서는 모델보다 먼저 파일 인벤토리와 데이터 계약을 확인해야 한다.

---

## 3. 왜 전체 2년 4개월이 아니라 6개월인가

전체 기간을 바로 분석하면 약 31GB를 여러 번 읽어야 한다. 이후 전처리 결과, Parquet, DB 데이터, 인덱스까지 생성하면 현재 개발 환경의 저장 공간도 부족해진다.

그래서 연속된 6개월 구간별로 데이터 포함 파일의 비율과 용량을 비교했다.

주요 후보는 다음과 같았다.

| 기간 | 데이터 포함 파일 | 전체 파일 | 가용률 | 크기 |
|---|---:|---:|---:|---:|
| 2018-09~2019-02 | 157 | 181 | 86.7% | 7.58GiB |
| 2019-01~2019-06 | 156 | 181 | 86.2% | 7.04GiB |
| **2019-02~2019-07** | **157** | **180** | **87.2%** | **6.69GiB** |
| 2019-09~2020-02 | 151 | 181 | 83.4% | 6.71GiB |

연속 6개월 중 데이터 가용률이 가장 높은 `2019_02`~`2019_07`을 MVP 분석 범위로 선택했다.

```text
분석 기간                 2019년 2월 ~ 2019년 7월
CSV 파일                 180개
데이터 포함 파일         157개
입력 크기                약 6.69GiB
전체 데이터 대비         약 23%
```

이 범위도 작지는 않지만, 전체 데이터보다 반복 실험이 훨씬 현실적이다.

---

## 4. 프로젝트 환경 준비

프로젝트 디렉터리로 이동한다.

```bash
cd smart_factory_mcp
```

Python 가상환경을 사용한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

EDA에 필요한 패키지를 설치한다.

```bash
python -m pip install pandas numpy matplotlib
```

설치 여부를 확인한다.

```bash
python -c "import pandas, numpy, matplotlib; print('EDA dependencies OK')"
```

원본 데이터와 디스크 공간도 확인한다.

```bash
du -sh SCADA/2019_{02,03,04,05,06,07}
df -h .
```

대용량 원본, 가상환경, 분석 결과는 Git에서 제외했다.

```gitignore
.venv/
SCADA/
*.7z
eda_output/
eda_output_*/
__pycache__/
.DS_Store
.env
```

---

## 5. EDA를 설계

처음에는 한 번의 순회로 모든 통계와 그래프 데이터를 만들려고 했다. 하지만 이 방식에는 문제가 있었다.

- 모든 센서의 샘플을 저장하면 메모리 사용량이 커진다.
- 앞부분 데이터부터 샘플이 채워져 전체 기간을 대표하지 못한다.
- 평균과 표준편차를 계산하기 전에는 정확한 3-Sigma 이상치를 셀 수 없다.

그래서 EDA를 두 번의 순회로 나눴다.

```text
Pass 1
├─ 파일 형식 및 오류 확인
├─ 전체 행 수와 센서 수
├─ 결측 및 변환 실패 집계
├─ 센서별 평균·표준편차·최솟값·최댓값
└─ 상위 센서 선정

Pass 2
├─ 전체 3-Sigma 이상치 계산
├─ 상위 센서 대표 샘플 수집
├─ 샘플링 간격 분석
└─ 그래프 데이터 생성
```

### 파일별 구분자 자동 감지

```python
def detect_separator(path):
    with path.open("rb") as file:
        header = file.readline().decode("utf-8", errors="replace")

    return ";" if header.count(";") > header.count(",") else ","
```

모든 CSV에 같은 `sep`를 적용하지 않고 헤더를 보고 파일별로 결정한다.

### 필요한 컬럼만 문자열로 읽기

```python
pd.read_csv(
    path,
    sep=separator,
    usecols=lambda name: (
        name.strip().lower() in {"id", "value", "unit", "timestamp"}
    ),
    dtype="string",
    chunksize=500_000,
    encoding_errors="replace",
)
```

처음부터 숫자로 읽지 않고 문자열로 읽는 이유는 혼합 소수점 형식을 직접 정규화하기 위해서다.

### 혼합 숫자 형식 정규화

현재 적용한 규칙은 다음과 같다.

```text
0,445       → 0.445
117.383     → 117.383
3.796.795   → 3796.795
1.234,56    → 1234.56
```

```python
cleaned = series.astype("string").str.strip()
cleaned = cleaned.str.replace(",", ".", regex=False)

multiple_dots = cleaned.str.count(r"\.").gt(1).fillna(False)
cleaned.loc[multiple_dots] = cleaned.loc[multiple_dots].str.replace(
    r"\.(?=.*\.)", "", regex=True
)

numeric = pd.to_numeric(cleaned, errors="coerce")
```

`3.796.795 → 3796.795` 규칙은 전체 원본에서 발견한 형식에 대한 가정이다. 이번 6개월 EDA에서는 `multiple_dot_value`가 0건이어서 실제 분석값에는 적용되지 않았다. 나머지 기간을 처리할 때 이 형식이 다시 나타나면 센서별 분포로 타당성을 재검증해야 한다.

### Timestamp 파싱

원본 Timestamp는 ISO 계열 고정 형식이다.

```python
pd.to_datetime(
    chunk["timestamp"],
    format="%Y-%m-%d %H:%M:%S.%f",
    errors="coerce",
)
```

원본에 시간대 정보가 없으므로 이 단계에서는 임의의 UTC 변환을 하지 않는다.

### Chunk 통계 병합

단순히 전체 `sum`과 `sum_sq`만 누적하면 큰 값에서 분산의 수치 오차가 커질 수 있다. 그래서 Chunk별 평균과 분산을 Chan 방식으로 병합했다.

또한 상위 센서 샘플은 앞에서부터 20,000개를 저장하는 대신 난수 우선순위를 이용해 전체 기간에서 균등하게 선택한다.

---

## 6. EDA 실행

현재 `eda.py`의 기본 분석 범위는 `2019_02`~`2019_07`이다.

```bash
.venv/bin/python eda.py
```

모든 옵션을 명시하면 다음과 같다.

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

로그까지 남기려면 `tee`를 사용한다.

```bash
.venv/bin/python eda.py 2>&1 | tee eda_2019_02_2019_07.log
```

EDA가 완료된 뒤 다음 파일이 생성되었다.

```text
eda_output_2019_02_2019_07/
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

결과 확인 명령:

```bash
sed -n '1,160p' eda_output_2019_02_2019_07/dataset_summary.json
sed -n '1,80p' eda_output_2019_02_2019_07/data_quality.csv
sed -n '1,30p' eda_output_2019_02_2019_07/sensor_summary.csv
sed -n '1,30p' eda_output_2019_02_2019_07/outlier_summary.csv
```

---

## 7. EDA 결과

6개월 EDA는 180개 CSV, 약 6.69GiB를 두 번 순회해 완료됐다. 파일 파싱이나 숫자·Timestamp 변환 실패 없이 약 1억 8,466만 행을 처리했다.

### 전체 요약

| 항목 | 결과 |
|---|---|
| 분석 대상 폴더 | `2019_02`~`2019_07` |
| 실제 측정 기간 | 180일 20시간 43분 |
| 처리 파일 수 | 180개 |
| 데이터 포함 파일 | 157개 |
| 헤더 전용 파일 | 23개 |
| 실패 파일 수 | 0개 |
| 전체 행 수 | 184,664,873행 |
| 실제 센서 수 | 149개 |
| 시작 Timestamp | `2019-01-31 00:00:00.143` |
| 종료 Timestamp | `2019-07-30 20:43:10.127` |

폴더 이름은 `2019_02`~`2019_07`이지만 실제 측정 기간은 하루 앞쪽으로 이동해 있다. 각 CSV가 전날 데이터를 다음 날 export하는 구조이기 때문이다. 이후 엄밀한 달력 기준 분할이 필요하면 Timestamp로 `2019-02-01 <= timestamp < 2019-08-01` 조건을 적용해야 한다.

메타데이터에는 센서가 147개라고 적혀 있지만 이 구간에서 실제 확인된 센서는 149개였다. 센서 추가·변경 또는 메타데이터 집계 기준 차이일 수 있으므로 이후 분석은 실제 관측된 149개를 기준으로 한다.

### 데이터 품질

| 항목 | 건수 | 비율 |
|---|---:|---:|
| 숫자 변환 실패 | 0 | 0% |
| Timestamp 변환 실패 | 0 | 0% |
| 다중 점 숫자 | 0 | 0% |
| `id` 결측 | 0 | 0% |
| `value` 결측 | 0 | 0% |
| `unit` 결측 | 0 | 0% |
| `timestamp` 결측 | 0 | 0% |

전체 데이터셋의 초기 파일에서는 `3.796.795` 같은 값이 발견됐지만, 선택한 6개월 구간에는 다중 점 숫자가 없었다. 따라서 현재 구간에서는 해당 정규화 규칙이 실제 값에 영향을 주지 않았다.

헤더 전용 파일 23개는 대부분 월요일 export 파일이었다. 센서별 최대 데이터 공백도 약 3일이어서, 이 파일들은 데이터 손상보다는 주말 또는 계획된 비가동 시간일 가능성이 높다. 다만 장비 운전 정보가 없으므로 이는 파일 날짜 패턴을 근거로 한 추론이다.

### 단위별 센서

| 단위 | 센서 수 | 레코드 수 | 비율 |
|---|---:|---:|---:|
| mg | 88 | 158,237,690 | 85.69% |
| l/min | 8 | 10,158,957 | 5.50% |
| bar | 8 | 7,168,184 | 3.88% |
| mm/s | 30 | 3,486,083 | 1.89% |
| l | 2 | 1,788,747 | 0.97% |
| °C | 9 | 1,452,791 | 0.79% |
| m³/h std. 추정 | 1 | 1,434,809 | 0.78% |
| m³ std. 추정 | 1 | 798,344 | 0.43% |
| Nl/min | 1 | 127,075 | 0.07% |
| mm | 1 | 12,193 | 0.01% |

원본의 `m�/h std.`, `m� std.`는 인코딩 손상으로 보이며 각각 `m³/h std.`, `m³ std.`로 추정된다. 이 두 단위는 전처리 단계에서 명시적인 매핑 규칙을 적용하되 원본 값도 함께 기록한다.

### 주요 그래프

![](https://velog.velcdn.com/images/22nsooda/post/793d2c05-5c7e-48fa-b532-69b25da4d582/image.png)

![](https://velog.velcdn.com/images/22nsooda/post/1e9ab42b-e9bf-45d3-a991-454f8f43c798/image.png)

![](https://velog.velcdn.com/images/22nsooda/post/f789743a-7ae0-497d-bc09-ff212f469316/image.png)

![](https://velog.velcdn.com/images/22nsooda/post/ac47c7ee-73ff-4042-85bf-f44a68823401/image.png)

현재 시계열 그래프는 전체 기간에서 균등하게 추출한 랜덤 샘플을 선으로 연결한 것이다. 멀리 떨어진 두 샘플 사이의 대각선은 실제 연속 변화가 아니라 시각화 과정에서 생긴 선일 수 있다. 다음 버전에서는 랜덤 샘플은 Scatter plot으로, 연속 시계열은 시간 리샘플링 후 Line plot으로 표현한다.

### EDA에서 확인한 문제

센서 값의 분포는 정규분포와 거리가 멀었다. 대표적인 mg 센서들은 낮은 값에 관측치가 몰리고 높은 값 방향으로 긴 꼬리를 갖고 있었다.

센서별 3-Sigma 이상치 비율은 다음과 같았다.

```text
평균 이상치 비율       1.18%
중앙 이상치 비율       1.08%
최대 이상치 비율       5.15% (센서 56)
3-Sigma 하한이 0 이하  110 / 149개 센서
```

mg 센서의 실제 최솟값은 양수인데 3-Sigma 하한이 음수가 된 경우가 많았다. 이는 양방향 3-Sigma가 사실상 높은 Spike만 탐지하고 있다는 뜻이다. 따라서 3-Sigma 결과는 고장 Label이 아니라 Isolation Forest와 비교하기 위한 Baseline으로만 사용한다.

대표 센서 92의 통계는 다음과 같다.

```text
평균             381.81 mg
표준편차         484.79 mg
최댓값           11,708.08 mg
3-Sigma 상한     1,836.19 mg
이상치 비율      2.29%
```

분포가 크게 치우쳐 있으므로 후속 분석에서는 다음 방법을 함께 검토한다.

- 상단 단방향 Threshold
- Median/IQR 또는 분위수 Threshold
- `log1p` 변환
- 운전/정지 상태 분리
- Window Feature 기반 Isolation Forest

---

## 8. TimescaleDB 설계

6개월 데이터가 약 6.69GiB라고 해서 전부 DB에 넣는 것이 정답은 아니다. 현재 디스크 여유와 MVP 목적을 고려해 다음과 같이 나눈다.

```text
원본 CSV
├─ 그대로 보관
│
├─ 선정 센서 원시값
│   └─ TimescaleDB sensor_reading
│
└─ 1분 Window Feature
    └─ TimescaleDB sensor_feature_1min
```

EDA 결과에서 다음 조건을 만족하는 진동 센서 3~5개를 후보로 선정했다.

- `mg` 또는 `mm/s` 단위
- 분석 기간 전체에 걸쳐 데이터가 존재
- 숫자 변환 성공률이 높음
- 단위가 일관됨
- 지나치게 긴 데이터 공백이 없음
- 값이 상수로 고정되지 않음
- 모델 학습에 충분한 관측치가 있음

### 선정 센서 후보

| 센서 ID | 단위 | 유효 행 수 | 중앙 수집 간격 | 선정 이유 |
|---|---|---:|---:|---|
| **92** | mg | 11,315,162 | 약 0.657초 | 가장 많은 데이터, 강한 Spike 패턴 |
| **109** | mg | 11,216,992 | 약 0.657초 | 낮은 진폭군 비교 대상 |
| **84** | mg | 10,712,815 | 약 0.657초 | 상대적으로 낮은 3-Sigma 이상치 비율 |
| 64 | mg | 10,805,721 | 약 0.657초 | 높은 변동성과 2.39% 이상치 비율 |
| 100 | mm/s | 539,009 | 추가 측정 필요 | 속도 센서 확장 실험 후보 |

```text
PRIMARY_SENSOR_IDS=92,109,84
COMPARISON_SENSOR_ID=64
VELOCITY_PILOT_SENSOR_ID=100
```

초기 모델은 센서 92, 109, 84를 각각 별도 모델로 학습한다. 센서 64는 고변동 비교군으로 사용하고, 센서 100은 mg 센서 파이프라인이 안정된 뒤 mm/s 모델을 검증할 때 추가한다.

상위 mg 센서의 중앙 수집 간격은 약 0.657초였지만 평균 간격은 약 1.4초였다. 최대 공백은 약 3일로, 주말 비가동 구간의 영향으로 해석된다. 센서별 운전 구간을 분리하지 않고 전체 평균 간격만 사용하면 Window 품질을 잘못 판단할 수 있다.

갑자기 커진 값이나 Spike는 이 단계에서 삭제하지 않는다. 이것이 센서 오류인지 실제 이상 징후인지 아직 알 수 없기 때문이다.

---

## 9. 원시값 대신 Window Feature 만들기

고주기 원시 센서값을 매번 모델에 전달하면 데이터가 너무 크고 센서별 수집 간격 차이도 처리하기 어렵다.

초기에는 1분 단위로 다음 Feature를 생성한다.

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

처리 흐름은 다음과 같다.

```text
선정 센서 원시값
→ 1분 단위 그룹화
→ 최소 샘플 수 확인
→ 통계 Feature 계산
→ Parquet 저장
→ TimescaleDB 적재
```

상위 mg 센서의 중앙 수집 간격은 약 0.657초로, 정상 수집 구간에서는 1분에 약 91개 관측치가 예상된다. 따라서 초기 Window는 1분으로 두고 최소 샘플 수는 3개가 아니라 30개로 시작한다. 품질 분포를 확인한 뒤 30~60개 범위에서 조정한다.

주말 비가동 구간은 보간하거나 forward fill하지 않는다. FFT는 샘플링이 충분히 빠르고 균일하다는 사실을 확인한 뒤 추가한다.

Parquet과 DB 적재용 패키지는 별도 requirements 파일로 설치했다.

```bash
.venv/bin/python -m pip install -r requirements-storage.txt
```

실행한 전처리 명령은 다음과 같다.

```bash
.venv/bin/python scripts/prepare_selected_data.py \
  --data-dir SCADA \
  --start-month 2019_02 \
  --end-month 2019_07 \
  --sensor-ids "92,109,84" \
  --output data/processed/selected_sensor_readings.parquet
```

Feature 생성 명령:

```bash
.venv/bin/python scripts/build_features.py \
  --input data/processed/selected_sensor_readings.parquet \
  --output data/processed/sensor_features_1min.parquet \
  --window 1min \
  --min-samples 30
```

실행 결과, 원본 184,664,873행에서 선정 센서 데이터 33,244,953행을 추출했다. 동일 Chunk 안에서 완전히 중복된 16행은 제거했다.

| 산출물 | 행 수 | 파일 크기 |
|---|---:|---:|
| `selected_sensor_readings.parquet` | 33,244,953 | 약 222MiB |
| `sensor_features_1min.parquet` | 387,741 | 약 15.3MiB |

1분 Window에는 평균 85.44개의 관측치가 포함됐고, 최소 30개·최대 184개였다.

---

## 10. TimescaleDB를 선택 이유

일반 PostgreSQL도 센서 데이터를 저장할 수 있다. TimescaleDB는 PostgreSQL 확장으로 동작하면서 시계열 데이터를 시간 Chunk로 나눠 관리하고, 센서별 기간 조회와 집계를 효율적으로 처리할 수 있다.

이 프로젝트에서는 다음 질문을 자주 처리해야 한다.

```text
특정 센서의 최근 1시간 데이터
특정 기간의 평균과 표준편차
최근 이상 구간
센서별 1분 Feature 변화
```

SQL과 PostgreSQL 생태계를 유지하면서 시계열 기능을 사용할 수 있다는 점이 MCP Tool 구현에도 잘 맞는다.

### 왜 Docker를 사용하는가

현재 Mac에는 PostgreSQL 18이 설치되어 있지만 TimescaleDB 확장은 설치되어 있지 않다. 로컬 확장을 직접 맞추는 대신 Docker로 PostgreSQL과 TimescaleDB 버전을 함께 고정하기로 했다.

```text
로컬 PostgreSQL       5432
Docker TimescaleDB    5433
```

Docker가 필수는 아니지만 다음 장점이 있다.

- PostgreSQL과 TimescaleDB 버전을 함께 고정
- 팀원이 동일한 환경을 재현
- 프로젝트 종료 후 컨테이너만 정리 가능
- 로컬 PostgreSQL 설정과 분리

---

## 11. Docker TimescaleDB 준비

Docker Desktop이 없다면 설치한다.

```bash
brew install --cask docker
open -a Docker
```

실행 상태를 확인한다.

```bash
docker version
docker compose version
docker info
```

`.env` 파일을 만든다.

```dotenv
POSTGRES_DB=smart_factory
POSTGRES_USER=smart_factory
POSTGRES_PASSWORD=<로컬 개발용 비밀번호>
POSTGRES_PORT=5433
```

`.env`는 Git에 올리지 않는다.

`docker-compose.yml`은 다음과 같이 구성한다.

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

공식 이미지의 `2.29.0-pg18` 태그를 고정했다. `latest`만 사용하면 PostgreSQL 메이저 버전까지 예상하지 못하게 바뀔 수 있기 때문이다.

DB를 실행한다.

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 timescaledb
```

TimescaleDB 확장을 활성화한다.

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

버전을 확인한다.

```bash
docker compose exec timescaledb \
  psql -U smart_factory -d smart_factory \
  -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
```

---

## 12. 데이터베이스 구조

Part 1에서 사용할 핵심 테이블은 다음과 같다.

```text
eda_run
├─ EDA 실행 범위와 전체 요약
│
eda_sensor_profile
├─ 센서별 평균·표준편차·3-Sigma
│
eda_quality_metric
├─ 변환 실패와 데이터 품질 수치
│
sensor_reading
├─ 선정 센서 원시값 Hypertable
│
sensor_feature_1min
└─ 1분 Feature Hypertable
```

### EDA 실행 정보

```sql
CREATE TABLE eda_run (
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
```

### 센서별 EDA 결과

```sql
CREATE TABLE eda_sensor_profile (
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
```

### 선정 센서 원시값

```sql
CREATE TABLE sensor_reading (
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

CREATE INDEX sensor_reading_sensor_time_idx
    ON sensor_reading (sensor_id, observed_at DESC);
```

### 1분 Feature

```sql
CREATE TABLE sensor_feature_1min (
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
```

원본 Timestamp에 시간대 정보가 없으므로 `observed_at`과 `window_start`는 `TIMESTAMP`로 저장한다. 이후 원본 시스템의 시간대를 확인할 수 있을 때 변환 정책을 추가한다.

---

## 13. 적재 흐름

EDA와 전처리가 완료된 뒤 아래 순서로 결과와 선정 센서 데이터를 적재했다.

```text
dataset_summary.json   → eda_run
sensor_summary.csv     → eda_sensor_profile
data_quality.csv       → eda_quality_metric
선정 센서 Parquet      → sensor_reading
1분 Feature Parquet    → sensor_feature_1min
```

대량 INSERT를 반복하기보다 psycopg의 `COPY`를 사용한다.

실행 명령:

```bash
set -a
source .env
set +a

.venv/bin/python scripts/load_timescaledb.py eda \
  --run-id eda_2019_02_2019_07_v1 \
  --input-dir eda_output_2019_02_2019_07

.venv/bin/python scripts/load_timescaledb.py readings \
  --input data/processed/selected_sensor_readings.parquet \
  --batch-size 100000

.venv/bin/python scripts/load_timescaledb.py features \
  --run-id eda_2019_02_2019_07_v1 \
  --input data/processed/sensor_features_1min.parquet \
  --batch-size 100000
```

적재 스크립트에는 다음 안전장치를 적용했다.

- EDA 실행별 `run_id` 관리
- 트랜잭션 실패 시 Rollback
- 중복 실행 방지
- Parquet 예상 행 수와 COPY 행 수 대조
- 비밀번호 로그 출력 금지

---

## 14. 적재 검증

DB에 들어갔다는 사실만으로 작업이 끝나지 않는다. 원본과 DB의 행 수, 시간 범위, 센서 목록이 일치하는지 확인해야 한다.

```sql
SELECT *
FROM eda_run
ORDER BY created_at DESC;

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

SELECT pg_size_pretty(
    pg_database_size(current_database())
) AS database_size;
```

### 적재 결과

| 항목 | 결과 |
|---|---|
| EDA Run ID | `eda_2019_02_2019_07_v1` |
| PostgreSQL 버전 | 18.4 |
| TimescaleDB 버전 | 2.29.0 |
| 적재 센서 | 92, 109, 84 |
| EDA Profile 행 수 | 149 |
| 원시 데이터 행 수 | 33,244,953 |
| Feature 행 수 | 387,741 |
| 데이터 시작 시각 | 2019-01-31 00:00:00.363 |
| 데이터 종료 시각 | 2019-07-30 20:43:10.127 |
| DB 크기 | 8,271MB |
| Hypertable 크기 | 원시값 8,161MB / Feature 99MB |
| Chunk 수 | 원시값 26 / Feature 7 |
| 실패 행 수 | 0 |
| Custom-format 백업 | `backups/smart_factory.dump`, 392MB |

---

## 15. 여기까지의 정리

이번 Part 1에서 가장 중요했던 것은 모델을 빨리 만드는 것이 아니었다.

실제 산업 데이터는 다음 문제를 먼저 해결해야 했다.

- 배포 파일 크기와 실제 압축 해제 크기가 다르다.
- 하나의 데이터셋 안에서도 CSV 형식이 다르다.
- 소수점 표기가 일관되지 않다.
- 헤더만 있는 파일이 상당수 존재한다.
- 센서와 실제 장비의 매핑이 없다.
- 고장 라벨이 없어 이상탐지 결과를 검증하기 어렵다.

그래서 전체 데이터를 바로 DB나 ML 모델에 넣는 대신 다음 흐름을 선택했다.

```text
파일 구조 확인
→ 6개월 범위 선정
→ 2-pass EDA
→ 품질 좋은 센서 3~5개 선정
→ 1분 Feature
→ TimescaleDB 적재
```

이번 EDA를 통해 실제 센서 수와 데이터 품질, 3-Sigma의 한계, 초기 센서 후보를 확정했다. 이어서 정제 Parquet과 1분 Feature를 생성하고 TimescaleDB 적재와 원본 대비 행 수 검증까지 완료했다.

---

## 다음 편 예고

Part 2에서는 TimescaleDB에 저장된 Feature를 이용해 이상탐지 모델을 만든다.

```text
3-Sigma Baseline
→ Isolation Forest
→ 시간 순서 Train / Validation / Test 분리
→ 이상 구간 비교
→ Anomaly Risk 정의
→ 모델 결과 DB 저장
```

모델의 `decision_function()` 값을 사용자에게 바로 보여주지 않고, 어떻게 일관된 위험 점수와 상태 등급으로 변환할지도 다룰 예정이다.

---