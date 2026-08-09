# Smart Factory MCP 문서

프로젝트 글과 실행 기록을 Part별로 보관한다. 프로젝트 전체 소개와 빠른 시작은 [루트 README](../README.md)를 참고한다.

| Part | 핵심 결과 |
|---|---|
| Part 1 | SCADA EDA, 센서 선정, TimescaleDB 적재 |
| Part 2 | 3-Sigma와 Isolation Forest 이상탐지 |
| Part 3 | Flask API와 read-only MCP Tool 7개 |
| Part 4 | ChromaDB RAG와 고정 LangGraph Router |
| Part 5 | Tool-Calling Agent와 실행 추적 Dashboard |

## Part 1 — EDA와 TimescaleDB

- [블로그 원고](part1/smart_factory_mcp_blog_part1.md)
- [재현용 Runbook](part1/eda_to_timescaledb_runbook.md)
- [EDA 결과 스냅샷](part1/results/)
- [EDA 이미지](part1/images/)

## Part 2 — 이상탐지

- [Part 2 안내](part2/README.md)
- [블로그 초안](part2/smart_factory_mcp_blog_part2.md)
- [이상탐지 실행 Runbook](part2/anomaly_detection_runbook.md)

TimescaleDB의 1분 Feature를 이용한 3-Sigma 및 Isolation Forest 실험을 다룬다.

## Part 3 — Flask API와 MCP 조회 Tool

- [Part 3 안내](part3/README.md)
- [블로그 초안](part3/smart_factory_mcp_blog_part3.md)
- [MCP 서버 실행 Runbook](part3/mcp_server_runbook.md)

센서 `92`, `109`, `84`의 기존 이상탐지 결과를 Flask 대시보드·REST API와 읽기 전용 MCP Tool 7개로 제공한다.

## Part 4 — RAG와 LangGraph

- [Part 4 안내](part4/README.md)
- [블로그 초안](part4/smart_factory_mcp_blog_part4.md)
- [실행 Runbook](part4/part4_runbook.md)

TimescaleDB 센서 상태와 ChromaDB 점검 문서를 LangChain Retriever와 LangGraph로 연결한다.

## Part 5 — Tool-Calling AI Agent

- [Part 5 안내](part5/README.md)
- [블로그 초안](part5/smart_factory_mcp_blog_part5.md)
- [실행 Runbook](part5/part5_runbook.md)

Part 4의 고정 `sensor | knowledge | hybrid` Router를 기준선으로 보존하고, Gemini가 MCP·RAG·조건부 Web Tool 결과를 관찰하며 다음 행동을 선택하는 bounded read-only Agent와 실행 추적 Dashboard를 추가한다.

## 문서 읽는 순서

처음 보는 경우 각 Part의 `README → 블로그 초안 → Runbook` 순서를 권장한다. 코드를 바로 실행하려면 Part 1·2로 DB를 구축한 뒤 Part 5 Runbook으로 Dashboard와 Agent를 실행한다.
