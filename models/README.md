# Generated Models

센서별 Isolation Forest 학습 artifact가 생성되는 로컬 디렉터리입니다.

```text
models/<model_run_id>/
├─ sensor_84.joblib
├─ sensor_92.joblib
├─ sensor_109.joblib
└─ run_summary.json
```

`joblib` 모델과 실행별 artifact는 재생성 가능하고 binary 크기가 커질 수 있어 Git에서 제외합니다. 모델 결과의 공개용 요약과 평가는 `docs/part2/results/`에 보관합니다.

생성과 평가:

```bash
python scripts/train_anomaly_models.py
python scripts/score_anomalies.py
python scripts/evaluate_anomalies.py
```

재현 절차와 고장 label이 없는 상황에서의 평가 원칙은 [Part 2 문서](../docs/part2/README.md)를 참고합니다.
