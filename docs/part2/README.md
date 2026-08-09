# Part 2 — 비지도 이상탐지

[문서 홈](../README.md) · 이전: [Part 1](../part1/README.md) · 다음: [Part 3](../part3/README.md)

Part 1에서 TimescaleDB에 적재한 센서 `92`, `109`, `84`의 1분 Feature로 통계 Baseline과 Isolation Forest를 비교한다.

> 구현 및 실행 완료: `iforest_2019_02_2019_07_v1`, 387,741개 Window DB 적재 완료

## 목표

```text
sensor_feature_1min
→ 센서별 시간 분리
→ 3-Sigma Baseline
→ Isolation Forest
→ 0~100 Risk Score
→ 이상 구간 검토
→ anomaly_result 저장
```

고장 라벨이 없으므로 정확도나 F1 점수를 최종 근거로 사용하지 않는다. 모델 간 일치도, 월별 탐지 안정성, 상위 이상 구간의 시각 검토를 중심으로 평가한다.

## 문서

- [블로그 초안](smart_factory_mcp_blog_part2.md)
- [실행 Runbook](anomaly_detection_runbook.md)
- [결과 파일 안내](results/README.md)
- [이미지 파일 안내](images/README.md)

## 완료 기준

- [x] 센서별 Train/Validation/Test 시간 분리가 재현 가능하다.
- [x] Train 구간만 사용해 모델을 학습한다.
- [x] 3-Sigma와 Isolation Forest 결과를 비교한다.
- [x] Validation 분포로 Risk Score와 상태 임계값을 고정한다.
- [x] Test 상위 이상 구간을 시각적으로 검토한다.
- [x] 모델 실행 정보와 이상 결과를 TimescaleDB에 저장한다.

## 관련 코드

- `scripts/train_anomaly_models.py`: 센서별 Isolation Forest 학습
- `scripts/score_anomalies.py`: 3-Sigma·Isolation Forest score와 상태 계산
- `scripts/evaluate_anomalies.py`: 비교 지표와 그래프 생성
- `scripts/load_anomaly_results.py`: model metadata와 결과 적재
- `sql/003_anomaly_schema.sql`: anomaly table과 index

학습된 `joblib`은 `models/`, 중간 Parquet은 `data/processed/`에 생성되며 Git에서 제외한다. 공개용 요약과 이미지는 이 문서 아래 `results/`, `images/`에 보관한다.
