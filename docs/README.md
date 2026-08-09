# Smart Factory MCP 문서

프로젝트 글과 실행 기록을 Part별로 보관한다.

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
