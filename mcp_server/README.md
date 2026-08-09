# MCP Server

TimescaleDB에 저장된 2019년 SCADA 이상탐지 결과를 read-only MCP Tool로 제공하는 계층입니다.

## 구조

```text
MCP Client
→ stdio transport
→ MCP Server
→ SensorRepository
→ TimescaleDB / PostgreSQL
```

| 파일 | 역할 |
|---|---|
| `server.py` | MCP Server 설정과 Tool 7개 등록 |
| `client.py` | 동기식 애플리케이션에서 사용하는 stdio MCP Client adapter |
| `repository.py` | 입력 검증과 파라미터화된 read-only SQL |
| `__init__.py` | Python package marker |

## Tool

```text
list_monitored_sensors
get_model_summary
get_sensor_status
get_abnormal_sensors
get_sensor_history
get_anomaly_detail
get_factory_summary
```

모든 응답은 historical timestamp와 `model_run_id`를 보존합니다. Risk Score는 비지도 모델의 이상 후보 점수이며 고장 확률이 아닙니다.

## 실행

```bash
source .venv/bin/activate
python -m mcp_server.server
```

서버는 stdio transport를 사용하므로 일반적으로 `SensorMCPClient` 또는 MCP Host가 subprocess로 실행합니다.

## 환경변수

```text
DATABASE_URL                   선택적 전체 연결 문자열
POSTGRES_DB                    DATABASE_URL이 없을 때 필수
POSTGRES_USER                  DATABASE_URL이 없을 때 필수
POSTGRES_PASSWORD              DATABASE_URL이 없을 때 필수
POSTGRES_PORT                  DATABASE_URL이 없을 때 필수
MCP_MODEL_RUN_ID               기본 model run 선택
```

MCP subprocess에는 DB 관련 환경변수만 전달하며 Gemini와 Tavily API 키는 전달하지 않습니다.

## 테스트

```bash
python scripts/mcp_smoke_test.py
```

세부 구현 과정은 [Part 3 문서](../docs/part3/README.md), Agent 연동은 [Part 5 문서](../docs/part5/README.md)를 참고합니다.
