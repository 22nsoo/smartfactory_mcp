# Part 3 — TimescaleDB 결과를 Flask API와 MCP Tool로 제공하기

[문서 홈](../README.md) · 이전: [Part 2](../part2/README.md) · 다음: [Part 4](../part4/README.md)

Part 2에서 저장한 센서 `92`, `109`, `84`의 이상탐지 결과를 Flask 대시보드·REST API와 읽기 전용 MCP Tool로 제공한다.

> 이 문서는 Part 3 구현 시점의 구조를 기록한다. 현재 Part 5 Dashboard의 센서 API는 MCP 경계를 일관되게 유지하도록 `Flask → SensorMCPClient → MCP Server → Repository` 경로를 사용한다.

## 구현 범위

```text
Browser / REST Client → Flask
                           ├─ 읽기 전용 Repository → TimescaleDB
MCP Client           → MCP ┘
```

Flask API:

- `GET /`
- `GET /health`
- `GET /api/model`
- `GET /api/sensors`
- `GET /api/sensors/<sensor_id>/status`
- `GET /api/sensors/<sensor_id>/history`
- `GET /api/abnormal-sensors`
- `GET /api/anomaly-detail`
- `GET /api/factory-summary`

구현된 Tool:

- `list_monitored_sensors`
- `get_model_summary`
- `get_sensor_status`
- `get_abnormal_sensors`
- `get_sensor_history`
- `get_anomaly_detail`
- `get_factory_summary`

## 문서

- [블로그 초안](smart_factory_mcp_blog_part3.md)
- [실행 Runbook](mcp_server_runbook.md)

## 코드

- `mcp_server/repository.py`: 검증과 파라미터화된 읽기 전용 SQL
- `mcp_server/server.py`: MCP 2.0 Tool 등록과 stdio 서버
- `web_app/app.py`: Flask App Factory와 REST API
- `web_app/templates/dashboard.html`: 센서 상태 화면
- `scripts/flask_smoke_test.py`: Flask API 테스트
- `scripts/mcp_smoke_test.py`: 실제 MCP Client 종단 간 테스트
- `requirements-part3.txt`: Part 3 전체 의존성

## 현재 검증 결과

```text
MCP SDK              2.0.0
Flask                3.1.3
Model Run ID         iforest_2019_02_2019_07_v1
센서                 92, 109, 84
결과 Window          387,741개
Tool                 7개
Flask API 테스트      통과
stdio 종단 간 테스트  통과
```

현재 Tool-Calling Agent와 Dashboard는 [Part 5 문서](../part5/README.md)를 참고한다.
