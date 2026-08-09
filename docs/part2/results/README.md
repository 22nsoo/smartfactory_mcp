# Part 2 결과 파일

[Part 2 안내](../README.md) · [결과 이미지](../images/README.md)

완료된 모델 실행의 다음 결과를 이 폴더에 저장한다.

```text
split_summary.csv
model_run_summary.json
sensor_metrics.csv
method_overlap.csv
monthly_anomaly_rate.csv
data_quality_effect.csv
top_anomaly_windows.csv
```

모델 바이너리는 재생성 가능하고 크기가 커질 수 있으므로 문서 폴더가 아니라 프로젝트 루트의 `models/`에 저장한다.

CSV와 JSON은 완료된 `iforest_2019_02_2019_07_v1` 실행을 GitHub에서 검토할 수 있도록 보관한 공개 snapshot이다. 원본 SCADA와 window별 전체 Parquet은 포함하지 않는다.
