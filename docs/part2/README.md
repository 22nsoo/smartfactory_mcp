# Part 2 — 비지도 이상탐지

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
