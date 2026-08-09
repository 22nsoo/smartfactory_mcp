# Part 5 — LangGraph Tool-Calling AI Agent

[문서 홈](../README.md) · [프로젝트 홈](../../README.md) · 이전: [Part 4](../part4/README.md)

Part 4의 고정 `sensor | knowledge | hybrid` Router를 기준선으로 보존하고, 사용자 질문과 이전 Tool 결과를 바탕으로 다음 행동을 선택하는 bounded read-only Agent를 추가한다.

## 구현 상태

```text
Message 기반 AgentState       완료
Gemini bind_tools             완료
LangGraph Agent ↔ Tools Loop  완료
MCP Tool 7개 wrapper          완료
ChromaDB 지식 검색 Tool        완료
조건부 Tavily Tool             완료
Tool trace / Citation          완료
최대 Step / 오류 Fallback      완료
Mock Agent 테스트              통과
실제 MCP / Flask 로컬 테스트   통과
AI Agent Dashboard             완료
MCP Dashboard API              완료
Sensor History SVG Chart       완료
Dashboard Mock 테스트          통과
온라인 외부 API 테스트         별도 승인 필요
```

## Agent가 사용할 수 있는 Tool

```text
MCP
├─ list_monitored_sensors
├─ get_model_summary
├─ get_sensor_status
├─ get_abnormal_sensors
├─ get_sensor_history
├─ get_anomaly_detail
└─ get_factory_summary

Local RAG
└─ search_maintenance_knowledge

Conditional Web
└─ search_web
```

`search_web`은 사용자가 웹·인터넷·외부·최신 자료 검색을 명시한 요청에만 모델에 노출한다. Tool 내부에서도 같은 권한을 다시 검사한다.

## 문서

- [실행 Runbook](part5_runbook.md)
- [블로그 초안](smart_factory_mcp_blog_part5.md)

## 주요 코드

- `rag/agent_workflow.py`
- `rag/tools.py`
- `mcp_server/client.py`
- `scripts/part5_agent_mocked_test.py`
- `scripts/part5_agent_smoke_test.py`
- `scripts/part5_agent_online_smoke_test.py`
- `scripts/part5_dashboard_test.py`
- `web_app/templates/dashboard.html`
- `web_app/static/dashboard.css`
- `web_app/static/js/api.js`
- `web_app/static/js/dashboard.js`

## Dashboard

Flask와 vanilla JavaScript 구조를 유지하면서 다음 정보를 한 화면에 연결한다.

```text
Factory Sensor Cards
→ Sensor Detail
→ Risk Score / RMS / Peak-to-Peak History
→ AI Agent Answer
→ MCP / RAG / Web Tool Trace
→ Local / Web Sources
```

`GET /api/dashboard`는 하나의 MCP Session에서 sensor 목록과 factory summary를 조회한다. 센서 선택 시에만 `get_sensor_status`와 `get_sensor_history`를 호출하며 차트는 외부 dependency 없는 SVG로 렌더링한다.

실행과 API 계약은 [Part 5 Runbook](part5_runbook.md), 구현 과정은 [블로그 초안](smart_factory_mcp_blog_part5.md)에서 자세히 설명한다.

## 안전 범위

Agent 자율성은 read-only Tool 선택과 정보 수집 순서에 한정한다. PLC 제어, 설비 정지, DB 쓰기와 알람 확인 처리는 포함하지 않는다.
