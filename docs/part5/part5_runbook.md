# Part 5 실행 가이드

## 1. 환경 준비

```bash
cd /Users/insu/Desktop/smartfactory_mcp
source .venv/bin/activate
python -m pip install -r requirements-part4.txt
docker compose up -d
python scripts/index_knowledge.py
```

Part 5는 Part 4와 같은 dependency를 사용하며 추가 업그레이드가 필요하지 않다.

`.env` 선택 설정:

```dotenv
GOOGLE_API_KEY=<Google AI Studio API 키>
GOOGLE_MODEL=gemini-2.5-flash
GOOGLE_MAX_TOKENS=1500
GOOGLE_THINKING_BUDGET=0
TAVILY_API_KEY=<Tavily API 키>
TAVILY_MAX_RESULTS=3
AGENT_MAX_STEPS=8
```

`AGENT_MAX_STEPS`는 1~20만 허용하며 기본값은 8이다.

## 2. 실행 구조

```text
START
  ↓
agent ── Tool 호출 없음 ──→ END
  │
  ├─ Tool 호출 + 한도 미도달 → tools → agent
  │
  └─ Tool 호출 + 한도 도달   → limit_fallback → END
```

Agent는 `ToolMessage`를 다시 읽으므로 첫 조회 결과가 부족할 때 다른 Tool을 선택할 수 있다.

```text
get_sensor_status
→ 결과 관찰
→ get_sensor_history
→ 결과 관찰
→ search_maintenance_knowledge
→ 최종 답변
```

DB 접근은 기존 경계를 유지한다.

```text
Agent Tool
→ SensorMCPClient
→ MCP stdio Server
→ SensorRepository
→ TimescaleDB
```

`rag/`에서 Repository나 SQL을 직접 호출하지 않는다.

## 3. Web 검색 권한

다음 표현처럼 사용자가 외부 검색을 명시한 경우에만 `search_web`을 Gemini에 bind한다.

```text
웹 검색
인터넷 검색
검색해
검색해서
최신 자료
외부 자료
```

Tool 실행 시에도 `web_search_allowed` 상태를 재검사한다. 일반 질문에는 Tool schema 자체가 모델에 제공되지 않는다.

## 4. 응답 필드

기존 `/api/ask` 필드를 유지하면서 Agent 관찰 필드를 추가한다.

```text
route                  항상 agent
agent_mode             tool_calling 또는 fallback mode
agent_step_count       LLM 판단 횟수
tool_trace             Tool 이름, 인자, 성공 여부
citations              local document / web 출처
sensor_data            MCP 결과
retrieved_document_count
web_search_used
generation_mode
generation_error
```

`route`, `router_mode`, `router_reason`은 기존 UI/API 호환을 위해 남겨 두지만 실행 경로를 결정하지 않는다.

## 5. 테스트

외부 API와 DB 없이 결정적인 Agent Loop를 검증한다.

```bash
python scripts/part5_agent_mocked_test.py
python scripts/part5_dashboard_test.py
```

검증 항목:

- 단일 MCP Tool 호출
- 상태 → 이력 → 지식 검색의 순차 호출
- 지식 검색만 사용하는 질문
- Web Tool 노출과 차단
- Tool 오류의 안전한 observation 변환
- 최대 Step 종료
- LLM 없는 deterministic fallback
- Flask `/api/ask` HTTP 200과 빈 질문 HTTP 400
- 대시보드 HTML·CSS·JavaScript asset 로딩
- Sensor 84/92/109 API contract
- MCP/RAG Tool type, summary와 citation excerpt
- MCP 장애 시 credential 없는 사용자용 오류 응답

실제 로컬 TimescaleDB와 MCP stdio 연결을 검증한다.

```bash
python scripts/part5_agent_smoke_test.py
```

실제 Gemini와 Tavily 연결은 센서 결과와 검색 문서 일부를 외부 서비스로 전송한다. 데이터 반출 정책을 확인하고 명시적으로 허용된 환경에서만 실행한다.

```bash
python scripts/part5_agent_online_smoke_test.py
```

## 6. Flask 실행

```bash
python -m web_app.app
```

```bash
curl -X POST http://127.0.0.1:5000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"센서 92가 왜 이상한지 분석해줘"}'
```

대시보드에는 기존 Router 정보 대신 실제 Tool 호출 순서를 표시한다.

Dashboard read-only API:

```text
GET /api/dashboard
GET /api/sensors/{sensor_id}/status
GET /api/sensors/{sensor_id}/history?hours=24&limit=200
GET /api/system/mcp
POST /api/ask
```

`/api/dashboard`는 `list_monitored_sensors`와 `get_factory_summary`를 하나의 MCP stdio Session에서 실행한다. 센서 상세, history와 MCP runtime 정보도 Repository를 직접 호출하지 않는다.

화면 구성:

```text
Factory Status + Sensor Cards
Sensor Detail + Historical Timestamp
Risk Score / RMS / Peak-to-Peak SVG Chart
Agent Chat + Tool Execution Trace
Local RAG / Web Source Cards
MCP Runtime + Architecture Modal
```

차트는 외부 JavaScript library 없이 SVG로 구현했으며 선택 센서에 대해서만 최대 200개 point를 요청한다.

## 7. Fallback과 제한

```text
google_gemini_tool_agent          정상 Tool-Calling Agent
deterministic_offline_fallback    API 키 없는 로컬 실행
deterministic_provider_fallback   Gemini 오류 또는 최대 Step 도달
```

Tool 오류는 stack trace, DB URL과 인증 정보를 제거하고 `tool_execution_failed` observation으로 전달한다. Gemini가 첫 호출부터 실패해 Tool 결과가 없다면 기존 키워드 기반 최소 조회와 안전 Template을 실행한다.

현재 MCP 호출은 개별 Agent Tool마다 stdio Session을 생성할 수 있다. 정확성과 격리를 우선한 구현이며, 지연이 문제가 되면 request-scoped Session 재사용을 후속으로 검토한다.
