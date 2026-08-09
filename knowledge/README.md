# RAG 데모 지식 문서

이 디렉터리의 Markdown은 Part 4 Retriever와 Part 5 `search_maintenance_knowledge` Tool이 사용하는 로컬 점검 지식이다. 제조사 매뉴얼이 아니라 RAG 파이프라인 검증을 위해 작성한 일반 가이드다.

| 문서 | 내용 |
|---|---|
| `vibration_triage.md` | 진동 상승 시 1차 확인 순서 |
| `data_quality_checks.md` | 결측·수집 간격·센서 변경 등 품질 점검 |
| `risk_score_interpretation.md` | Risk Score의 의미와 금지 표현 |
| `welding_line_inspection.md` | 용접 생산라인 점검 관점 |

`##` 제목 단위로 chunking한 뒤 다음 명령으로 ChromaDB를 생성한다.

```bash
python scripts/index_knowledge.py
```

생성된 `data/vector_db/`는 Git에서 제외한다. 문서를 수정하면 인덱싱 명령을 다시 실행한다.

- 실제 설비 작업은 현장 안전 규정과 제조사 매뉴얼을 우선한다.
- Lockout/Tagout 등 에너지 차단 절차는 자격을 갖춘 작업자가 수행한다.
- 모델의 이상 점수만으로 설비 정지나 부품 교체를 결정하지 않는다.
- 센서 ID와 실제 설비 위치의 매핑은 데이터셋에 포함되어 있지 않다.

관련 문서: [Part 4](../docs/part4/README.md) · [Part 5](../docs/part5/README.md)
