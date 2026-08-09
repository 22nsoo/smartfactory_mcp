# 실행 스크립트

데이터 준비부터 이상탐지, MCP/RAG 검증과 Dashboard 테스트까지 재현 가능한 진입점을 모아 둔 디렉터리입니다. 모든 명령은 저장소 루트에서 실행합니다.

## 데이터와 Feature

| 스크립트 | 역할 |
|---|---|
| `prepare_selected_data.py` | 원본 SCADA에서 센서 `92`, `109`, `84`를 Parquet으로 추출 |
| `build_features.py` | 1분 window Feature 생성 |
| `load_timescaledb.py` | EDA 결과, 원시값과 Feature를 TimescaleDB에 적재 |

## 이상탐지

| 스크립트 | 역할 |
|---|---|
| `anomaly_common.py` | 공통 Feature, 상태 임계값과 I/O helper |
| `train_anomaly_models.py` | 센서별 Isolation Forest 학습 |
| `score_anomalies.py` | 3-Sigma와 Isolation Forest 점수 산출 |
| `evaluate_anomalies.py` | 평가 CSV·JSON·시각화 생성 |
| `load_anomaly_results.py` | 모델 metadata와 결과를 TimescaleDB에 적재 |

## MCP, RAG와 Agent

| 스크립트 | 역할 |
|---|---|
| `mcp_smoke_test.py` | 실제 MCP stdio Tool 종단 간 검증 |
| `flask_smoke_test.py` | Flask REST API 회귀 검증 |
| `index_knowledge.py` | `knowledge/*.md`를 ChromaDB에 인덱싱 |
| `part4_smoke_test.py` | Part 4 오프라인 RAG 검증 |
| `part4_mocked_integration_test.py` | Gemini/Tavily mock 통합 검증 |
| `part4_online_smoke_test.py` | Part 4 실제 외부 API 검증 |
| `part5_agent_mocked_test.py` | 동적 multi-step Agent와 안전 정책 검증 |
| `part5_agent_smoke_test.py` | 실제 MCP/Flask offline Agent 검증 |
| `part5_agent_online_smoke_test.py` | 실제 Gemini/Tavily Agent 검증 |
| `part5_dashboard_test.py` | Dashboard asset·API·오류 계약 검증 |

## 권장 실행 순서

전체 데이터 파이프라인은 다음 문서를 따릅니다.

1. [Part 1 Runbook](../docs/part1/eda_to_timescaledb_runbook.md)
2. [Part 2 Runbook](../docs/part2/anomaly_detection_runbook.md)
3. [Part 3 Runbook](../docs/part3/mcp_server_runbook.md)
4. [Part 4 Runbook](../docs/part4/part4_runbook.md)
5. [Part 5 Runbook](../docs/part5/part5_runbook.md)

`online_smoke_test`는 센서 결과와 검색 문서를 외부 서비스에 전송할 수 있으므로 명시적으로 허용된 환경에서만 실행합니다.
