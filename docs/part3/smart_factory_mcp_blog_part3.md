---
title: "[Smart Factory MCP #3] TimescaleDB 결과를 Flask API와 MCP Tool로 연결하기"
description: "센서 3개의 SCADA 이상탐지 결과를 Flask 대시보드·REST API와 읽기 전용 MCP Tool로 제공한 과정을 정리합니다."
tags:
  - Smart Factory
  - SCADA
  - MCP
  - Flask
  - Python
  - TimescaleDB
  - PostgreSQL
published: false
---

# [Smart Factory MCP #3] TimescaleDB 결과를 Flask API와 MCP Tool로 연결하기

> 이 글은 스마트 팩토리 SCADA 이상탐지 + MCP 프로젝트의 Part 3다.  
> Part 2에서 생성한 센서 `92`, `109`, `84`의 이상탐지 결과를 Flask 대시보드·REST API와 읽기 전용 MCP Tool로 제공한다.

## 들어가며

Part 2까지는 Python 스크립트와 SQL로 결과를 확인했다.

```text
1분 Feature       387,741개
Isolation Forest  센서별 모델 3개
이상탐지 결과      anomaly_result
```

하지만 LLM이 데이터베이스 구조와 SQL을 직접 알아야 한다면 애플리케이션 결합도가 높아진다. 자연어 질문을 받는 계층과 실제 데이터 조회 계층 사이에 명확한 Tool 계약이 필요했다.

Part 3의 목표는 다음 질문을 브라우저, REST Client와 MCP Client에서 동일하게 처리하는 것이다.

```text
모니터링 중인 센서는 무엇인가요?
센서 92의 마지막 상태는 어떤가요?
마지막 시점에 이상 상태인 센서는 무엇인가요?
센서 92의 최근 24시간 상태를 보여주세요.
특정 이상 Window에서 어떤 Feature가 변했나요?
```

---

## 1. Part 3 아키텍처

```text
Browser / REST Client
        │
        ▼
      Flask ─────────┐
                     ▼
MCP Client → MCP → 읽기 전용 Repository
                     │
                     ▼
              PostgreSQL / TimescaleDB
```

Flask와 MCP 서버는 모델을 다시 학습하거나 추론하지 않는다. Part 2에서 DB에 저장한 결과를 같은 Repository로 조회해 일관된 구조로 반환한다.

사용한 버전은 `Flask==3.1.3`, `mcp==2.0.0`이다.

```text
mcp_server/
├── __init__.py
├── repository.py
└── server.py

web_app/
├── app.py
├── templates/dashboard.html
└── static/dashboard.css

scripts/
├── flask_smoke_test.py
└── mcp_smoke_test.py
```

---

## 2. 어떤 Tool을 만들었는가

총 7개의 Tool을 구현했다.

| Tool | 역할 |
|---|---|
| `list_monitored_sensors` | 모델에 포함된 센서 목록 |
| `get_model_summary` | 모델과 데이터 범위 요약 |
| `get_sensor_status` | 센서의 마지막 저장 상태 |
| `get_abnormal_sensors` | 마지막 시점의 이상 센서 목록 |
| `get_sensor_history` | 센서의 시간별 상태 이력 |
| `get_anomaly_detail` | 특정 Window의 점수와 Feature 근거 |
| `get_factory_summary` | 마지막 상태별 센서 개수 |

임의 SQL을 받는 Tool은 만들지 않았다. LLM이 테이블 전체를 조회하거나 데이터를 변경하는 일을 막고, 필요한 질문만 명시적인 함수로 제공하기 위해서다.

---

## 3. Flask API와 대시보드

Flask는 App Factory 형태로 구성하고 Repository를 주입할 수 있게 만들었다.

```python
from flask import Flask, jsonify

def create_app(repository):
    app = Flask(__name__)

    @app.get("/api/sensors/<sensor_id>/status")
    def sensor_status(sensor_id: str):
        return jsonify(repository.get_sensor_status(sensor_id))

    return app
```

구현한 주요 API는 다음과 같다.

| Method | 경로 | 기능 |
|---|---|---|
| GET | `/` | 센서 상태 대시보드 |
| GET | `/health` | DB 연결 상태 |
| GET | `/api/sensors` | 모니터링 센서 목록 |
| GET | `/api/sensors/<id>/status` | 마지막 센서 상태 |
| GET | `/api/sensors/<id>/history` | 센서 상태 이력 |
| GET | `/api/abnormal-sensors` | 이상 센서 목록 |
| GET | `/api/anomaly-detail` | 특정 Window 상세 |
| GET | `/api/factory-summary` | 상태별 센서 수 |

대시보드는 별도의 프론트엔드 프레임워크 없이 Jinja Template과 JavaScript `fetch()`로 API를 호출한다. 화면 상단에는 반드시 데이터 기준 시각을 표시한다.

```bash
python -m web_app.app
```

```text
http://127.0.0.1:5000/
```

---

## 4. MCP 2.0 서버 등록

MCP 2.0에서는 `MCPServer`에 Python 함수를 Tool로 등록할 수 있다.

```python
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="smart-factory-scada",
    instructions=(
        "이 서버의 값은 2019년 과거 데이터다. "
        "항상 as_of 시각과 함께 설명한다."
    ),
)

@mcp.tool(structured_output=True)
def get_sensor_status(sensor_id: str) -> dict:
    return repository.get_sensor_status(sensor_id)
```

서버는 `stdio` 방식으로 실행한다.

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

HTTP 포트를 외부에 공개할 필요가 없고, MCP Client가 자식 프로세스로 서버를 실행할 수 있다.

---

## 5. 조회 계층을 분리한 이유

MCP Tool 안에 긴 SQL을 직접 작성하면 테스트와 재사용이 어려워진다. 그래서 Tool 등록과 DB 조회를 분리했다.

```text
server.py
└─ Tool 이름·설명·입력 계약

repository.py
└─ 입력 검증·SQL·결과 직렬화
```

Repository는 연결할 때마다 다음 설정을 적용한다.

```sql
SET TRANSACTION READ ONLY;
```

SQL 값은 문자열 결합이 아니라 psycopg 파라미터로 전달한다.

```python
connection.execute(
    """
    SELECT ...
    FROM anomaly_result
    WHERE model_run_id = %s AND sensor_id = %s
    """,
    (model_run_id, sensor_id),
)
```

센서 ID는 숫자만 허용하고, 이력 조회는 최대 744시간과 1,000개 Window로 제한했다.

---

## 6. Risk Score만 반환하지 않기

Part 2에서 센서 92는 낮은 표본 수와 수집 공백의 영향을 받을 수 있음을 확인했다. 따라서 상태 조회는 Risk Score 외에도 다음 값을 함께 반환한다.

```text
sample_count
previous_window
gap_minutes
mean, std, min_value, max_value
rms, peak_to_peak, slope
sigma_detected_features
```

센서 84의 마지막 저장 상태를 조회한 실제 응답 일부는 다음과 같다.

```json
{
  "sensor_id": "84",
  "as_of": "2019-07-30 20:42:00",
  "is_historical_data": true,
  "unit": "mg",
  "risk_score": 57.7733,
  "status": "NORMAL",
  "sample_count": 63,
  "gap_minutes": 1.0,
  "sigma_detected_features": []
}
```

이 구조를 사용하면 Part 4의 Agent가 `WARNING`이라는 단어만 확대 해석하지 않고 데이터 품질과 Feature 근거를 함께 설명할 수 있다.

---

## 7. 과거 데이터와 실시간 상태 구분

이 프로젝트의 데이터는 실시간 스트림이 아니다. 현재 저장된 마지막 Window는 `2019-07-30 20:42:00`이다.

마지막 저장 시점의 실제 조회 결과는 다음과 같았다.

| 센서 | 상태 | Risk Score |
|---|---|---:|
| 84 | NORMAL | 57.77 |
| 92 | DEGRADING | 81.83 |
| 109 | NORMAL | 16.92 |

따라서 Tool 응답에는 항상 다음 필드를 포함한다.

```json
{
  "as_of": "2019-07-30 20:42:00",
  "is_historical_data": true
}
```

LLM도 이를 “현재 공장 상태”가 아니라 “데이터셋의 마지막 기록 상태”로 표현해야 한다.

---

## 8. 실행과 종단 간 검증

의존성을 설치한다.

```bash
source .venv/bin/activate
python -m pip install -r requirements-part3.txt
```

TimescaleDB를 실행한다.

```bash
docker compose up -d
docker compose ps
```

Flask API 테스트와 MCP Client 스모크 테스트를 실행한다.

```bash
python scripts/flask_smoke_test.py
python scripts/mcp_smoke_test.py
```

테스트에서는 Flask Test Client와 실제 MCP stdio Client로 다음을 확인했다.

```text
Flask 대시보드 렌더링       성공
Flask 정상 API 9개          HTTP 200
잘못된 입력 2개             HTTP 400
Tool 목록 조회               성공
등록 Tool                    7개
get_sensor_status("84")      성공
구조화된 응답                 성공
TimescaleDB 조회             성공
```

---

## 9. 현재 한계

- 센서 ID와 실제 장비 위치의 매핑이 없다.
- 고장 라벨이 없어 이상 상태가 실제 고장을 의미하지 않는다.
- 공개 데이터셋의 마지막 시점까지 조회하는 구조이며 실시간 수집이 아니다.
- MCP는 상태 데이터를 제공하지만 정비 방법이나 원인 지식은 아직 제공하지 않는다.
- Flask 개발 서버는 로컬 데모용이며 외부 공개용 인증과 HTTPS는 아직 없다.
- `100 - Risk Score`를 임의의 Health Score로 표현하지 않았다. Risk Score는 Validation 분포상의 상대적 위치이지 설비 수명을 뜻하지 않기 때문이다.

---

## 10. 정리

Part 3에서는 하나의 DB 조회 계층을 Flask와 MCP 두 인터페이스에서 재사용했다.

```text
TimescaleDB
→ 읽기 전용 Repository
├─ Flask 대시보드·REST API
└─ MCP Tool 7개
→ HTTP·stdio Client 검증
→ 구조화된 센서 상태 응답
```

중요한 점은 단순히 SQL 결과를 LLM에 넘기는 것이 아니었다. 기준 시각, 과거 데이터 여부, 표본 수와 수집 공백을 함께 반환해야 모델 결과의 한계를 보존할 수 있었다.

---

## 다음 편 예고

Part 4에서는 MCP의 센서 상태와 정비 문서 RAG를 연결한다.

```text
사용자 자연어 질문
→ LangGraph 질의 분류
→ MCP 상태 조회
→ RAG 점검 문서 검색
→ 상태·근거·점검 항목을 결합한 답변
```

## 참고 자료

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [TimescaleDB Documentation](https://docs.timescale.com/)
