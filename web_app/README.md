# Flask API와 AI Agent Dashboard

센서 조회 REST API, `/api/ask` Agent endpoint와 실행 추적 Dashboard를 제공합니다.

## 구성

| 경로 | 역할 |
|---|---|
| `app.py` | Flask App Factory와 read-only API |
| `templates/dashboard.html` | Dashboard semantic markup |
| `static/dashboard.css` | Desktop·tablet·mobile 반응형 UI |
| `static/js/api.js` | Frontend API wrapper |
| `static/js/dashboard.js` | 센서 선택, SVG chart, Agent trace와 source rendering |

## Dashboard 기능

- 센서 `84`, `92`, `109`의 마지막 저장 상태 카드
- Risk Score, RMS, Peak-to-Peak 상세와 6·12·24시간 SVG chart
- Agent 답변과 MCP/RAG/Web Tool 실행 순서
- Tool 인자, 성공 여부와 안전한 결과 요약
- 로컬 RAG citation excerpt와 Web URL
- 실제 MCP stdio Tool 목록 기반 runtime 상태
- Architecture modal과 Raw API response viewer

## API

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/api/dashboard` | 한 MCP Session에서 sensor 목록과 factory summary 조회 |
| `GET` | `/api/sensors/<id>/status` | 센서 마지막 저장 상태 |
| `GET` | `/api/sensors/<id>/history` | 마지막 timestamp 기준 historical window |
| `GET` | `/api/system/mcp` | MCP stdio runtime과 Tool 목록 |
| `POST` | `/api/ask` | Tool-Calling Agent 질의 |

Flask의 센서 데이터 endpoint는 Repository를 직접 호출하지 않고 `SensorMCPClient`를 거칩니다.

## 실행

```bash
source .venv/bin/activate
python -m web_app.app
```

브라우저: `http://127.0.0.1:5000/`

## 테스트

```bash
python scripts/flask_smoke_test.py
python scripts/part5_dashboard_test.py
```

Dashboard는 2019년 저장 데이터만 표시합니다. `Available` 표시는 실제 MCP server의 Tool 목록 조회가 성공했을 때만 사용합니다.
