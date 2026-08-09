# Generated Data

실행 중 생성되는 중간 데이터의 로컬 저장 위치입니다. 대용량·재생성 가능 파일은 Git에 포함하지 않습니다.

```text
data/
├─ processed/   선정 센서 raw Parquet, 1분 Feature, anomaly score
└─ vector_db/   ChromaDB persistent index
```

생성 명령:

```bash
python scripts/prepare_selected_data.py
python scripts/build_features.py
python scripts/score_anomalies.py
python scripts/index_knowledge.py
```

공개 가능한 결과 요약은 `docs/part1/results/`, `docs/part2/results/`, `docs/part4/results/`에 별도 snapshot으로 보관합니다.
