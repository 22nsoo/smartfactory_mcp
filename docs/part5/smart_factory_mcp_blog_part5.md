---
title: "[Smart Factory MCP #5] 고정 Workflow를 Tool-Calling AI Agent로 전환하기"
description: "Gemini가 MCP 센서 조회, ChromaDB 지식 검색과 조건부 Tavily 검색을 동적으로 선택하도록 LangGraph Agent Loop를 구현한 과정을 정리합니다."
tags:
  - Smart Factory
  - MCP
  - AI Agent
  - LangGraph
  - Tool Calling
  - RAG
published: false
---

# [Smart Factory MCP #5] 고정 Workflow를 Tool-Calling AI Agent로 전환하기

> Part 4의 고정 Router 기반 RAG Workflow를 기준선으로 보존한다.  
> Part 5에서는 LLM이 이전 Tool 결과를 관찰하고 다음 Tool을 선택하는 bounded read-only Agent를 추가한다.

## 들어가며

Part 4에서는 질문을 `sensor`, `knowledge`, `hybrid`로 한 번 분류한 뒤 코드에 정의된 경로를 실행했다.

```text
질문
→ Router
→ 미리 정한 sensor / knowledge / hybrid 경로
→ 답변
```

센서 상태와 점검 문서를 안정적으로 결합하기에는 적합했지만, 첫 번째 조회 결과를 보고 추가 정보가 필요한지 다시 판단하지는 못했다. 예를 들어 “센서 92가 왜 이상한지 분석해줘”라는 질문은 마지막 상태만으로 충분할 수도 있고, 이력과 점검 지식까지 필요할 수도 있다.

Part 5에서는 이 판단을 Tool-Calling Agent Loop로 옮겼다.

```text
질문
→ LLM이 Tool 선택
→ Tool 결과 관찰
→ 다음 Tool 필요 여부 재판단
→ 충분하면 답변
```

Agent의 자율성은 read-only 정보 조회 순서에만 적용한다. 설비 제어와 데이터 쓰기는 포함하지 않는다.

---

## 1. 전체 구조

```text
사용자 질문
    │
    ▼
LangGraph Tool-Calling Agent
    │
    ├─ MCP Sensor Tools ──→ MCP stdio ──→ TimescaleDB
    ├─ Knowledge Tool ─────→ ChromaDB
    └─ Web Tool ───────────→ Tavily
           ▲                    사용자가 명시한 경우만 제공
           │
       ToolMessage
           │
           └──────────────→ Agent
                                │
                          추가 Tool 필요?
                           ├─ Yes → 반복
                           └─ No  → Final Answer
```

LangGraph는 세 노드로 구성했다.

```text
START → agent → tools → agent → ... → END
                │
                └─ 최대 단계 도달 → limit_fallback → END
```

Part 4의 Router는 오프라인 fallback에만 남아 있으며 온라인 Tool 실행 순서를 결정하지 않는다.

---

## 2. MCP Tool을 Agent Tool로 감싸기

기존 MCP Server의 read-only Tool 7개는 변경하지 않았다.

```text
list_monitored_sensors
get_model_summary
get_sensor_status
get_abnormal_sensors
get_sensor_history
get_anomaly_detail
get_factory_summary
```

새 `rag/tools.py`에서 LangChain Tool로 감싸되 DB Repository를 직접 호출하지 않는다.

```python
@tool("get_sensor_status", response_format="content_and_artifact")
def get_sensor_status(sensor_id: str):
    payload = sensor_client.call_tool(
        "get_sensor_status",
        {"sensor_id": sensor_id},
    )
    return json.dumps(payload), {
        "kind": "mcp",
        "tool": "get_sensor_status",
        "data": payload,
    }
```

호출 경계는 그대로다.

```text
Agent → SensorMCPClient → MCP Server → Repository → TimescaleDB
```

`content`는 LLM이 읽고, `artifact`는 API가 sensor data, citation과 Tool trace를 안정적으로 복원하는 데 사용한다.

---

## 3. ChromaDB 검색도 Tool로 노출하기

Part 4에서는 Workflow의 knowledge 노드가 Retriever를 직접 실행했다. Part 5에서는 `search_maintenance_knowledge` Tool로 바꿨다.

```json
{
  "query": "진동 위험 점검",
  "documents": [
    {
      "source": "vibration_triage.md",
      "title": "진동 상승 시 확인 순서",
      "chunk": 2,
      "excerpt": "..."
    }
  ]
}
```

LangChain `Document` 객체를 그대로 노출하지 않고 JSON 직렬화 가능한 구조로 정규화했다. 최종 API의 citation은 `(type, source, chunk)`를 기준으로 중복을 제거한다.

---

## 4. Message 기반 AgentState

Tool 호출과 observation을 대화 기록으로 누적한다.

```python
class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    web_search_allowed: bool
    agent_step_count: int
    generation_mode: str
    generation_error: str
```

Agent가 만든 `AIMessage.tool_calls`는 `ToolNode`로 전달된다. 실행 결과는 `ToolMessage`가 되어 다시 Agent의 입력에 포함된다. 이 반복 때문에 첫 결과에 따라 다음 행동을 바꿀 수 있다.

```text
get_sensor_status(92)
→ 마지막 상태만으로 추세 판단이 어려움
→ get_sensor_history(92)
→ 진동 점검 지식 필요
→ search_maintenance_knowledge(...)
→ Final Answer
```

---

## 5. Web Tool은 명시적으로 허용할 때만 제공하기

LLM이 임의로 외부 검색 비용이나 데이터 전송을 발생시키면 안 된다. 요청에 `웹 검색`, `검색해`, `최신 자료` 같은 명시적 표현이 있을 때만 `search_web`을 모델에 bind한다.

```text
일반 질문
→ MCP + Knowledge Tool만 bind

명시적 Web 질문
→ MCP + Knowledge + Web Tool bind
```

Tool 내부에서도 `web_search_allowed`를 다시 검사한다. 모델에 Tool을 숨기는 1차 경계와 실행 시 권한을 확인하는 2차 경계를 함께 적용했다.

---

## 6. 무한 반복과 Tool 오류 방지

기본 Agent 최대 단계는 8이다.

```dotenv
AGENT_MAX_STEPS=8
```

마지막 모델 응답에 Tool 호출이 있어도 한도에 도달했다면 실행하지 않고 `limit_fallback`으로 이동한다. 단순히 Graph를 종료하지 않는 이유는 Tool 호출 메시지에는 사용자에게 보여줄 완전한 답변이 없을 수 있기 때문이다.

Tool 오류는 전체 Graph를 중단하지 않고 안전한 observation으로 바꾼다.

```json
{
  "ok": false,
  "error": "tool_execution_failed",
  "error_type": "MCPToolError"
}
```

DB URL, 비밀번호와 stack trace는 LLM이나 API 응답에 포함하지 않는다.

---

## 7. API 호환성과 Tool trace

Flask `/api/ask`는 기존 필드를 유지하면서 Agent 필드를 추가했다.

```json
{
  "route": "agent",
  "agent_mode": "tool_calling",
  "agent_step_count": 4,
  "tool_trace": [
    {"step": 1, "tool": "get_sensor_status", "status": "success"},
    {"step": 2, "tool": "get_sensor_history", "status": "success"},
    {"step": 3, "tool": "search_maintenance_knowledge", "status": "success"}
  ],
  "generation_mode": "google_gemini_tool_agent"
}
```

기존 `route`와 Router 관련 필드는 호환 목적으로 남아 있지만 실행 제어에는 사용하지 않는다. 대시보드는 Router 분류 대신 실제 호출된 Tool 순서를 표시한다.

---

## 8. Offline fallback

LLM이 없으면 자율 Tool 선택은 불가능하다. 이 경우 Part 4의 결정론적 분류를 fallback으로 사용해 센서 조회, 로컬 검색과 안전 Template을 제공한다.

```text
google_gemini_tool_agent          정상 Agent Loop
deterministic_offline_fallback    API 키 없는 로컬 실행
deterministic_provider_fallback   Provider 오류 또는 단계 한도 도달
```

온라인 Agent가 첫 호출부터 실패해 observation이 하나도 없다면 deterministic 경로로 최소 근거를 수집한다. 이미 Tool 결과가 있다면 확보된 observation만으로 안전 Template을 만든다.

---

## 9. 검증

Mock LLM이 호출 순서를 결정하도록 만들어 실제 외부 API 없이 Agent Loop를 검증했다.

```bash
python scripts/part5_agent_mocked_test.py
python scripts/part5_agent_smoke_test.py
```

검증 결과:

```text
단일 MCP Tool                 성공
상태 → 이력 → 지식 Multi-step 성공
Knowledge only               성공
Web Tool 허용/차단            성공
Tool 오류 후 답변             성공
최대 Step 종료                성공
Offline fallback             성공
실제 MCP → TimescaleDB        성공
Flask /api/ask               HTTP 200
빈 질문                       HTTP 400
```

실제 Gemini와 Tavily 테스트는 센서 결과와 검색 문서 일부가 외부 서비스로 전송되므로 데이터 반출이 허용된 환경에서 별도로 실행한다.

```bash
python scripts/part5_agent_online_smoke_test.py
```

---

## 10. AI Agent Dashboard

Part 5에서는 Agent 실행을 화면에서 확인할 수 있도록 Flask 대시보드도 함께 고도화했다.

```text
Factory Status
├─ Sensor 84 / 92 / 109 cards
├─ Sensor Detail
└─ Historical Feature Chart

AI Agent
├─ Answer
├─ MCP / RAG / WEB Tool Trace
└─ Local / Web Sources
```

대시보드 최초 로딩은 `GET /api/dashboard` 한 번으로 sensor 목록과 factory summary를 받는다. 이 endpoint는 Repository를 직접 호출하지 않고 하나의 MCP stdio Session에서 `list_monitored_sensors`와 `get_factory_summary`를 순서대로 실행한다.

센서 카드를 선택하면 `get_sensor_status`와 `get_sensor_history`를 호출한다. History는 마지막 저장 timestamp 기준 이전 6·12·24시간을 선택할 수 있고 Risk Score, RMS, Peak-to-Peak을 외부 차트 dependency 없는 SVG로 표시한다.

Agent 응답의 `tool_trace`에는 내부 reasoning 대신 다음 정보만 표시한다.

```text
Tool name
MCP / RAG / WEB type
arguments
success / error status
safe result summary
execution order
```

Citation에는 local document excerpt를 추가해 source card를 펼쳤을 때 검색 근거를 확인할 수 있다. Web URL은 `http`와 `https`만 링크로 허용하며 API key, DB URL과 환경변수는 화면이나 Raw response에 넣지 않는다.

MCP badge는 고정된 Connected 문구가 아니다. `GET /api/system/mcp`가 실제 stdio server의 Tool 목록을 조회한 경우에만 `Available`로 표시한다.

Dashboard 검증:

```bash
python scripts/part5_dashboard_test.py
```

```text
페이지 / static asset             성공
Sensor selection API              성공
History 200-point contract        성공
Agent Tool Trace / Source contract 성공
MCP runtime Tool 7개               성공
Friendly error response           성공
Desktop / mobile CSS breakpoint    확인
```

---

## 11. 현재 한계

- 각 Agent Tool 호출이 별도 MCP stdio Session을 생성할 수 있다.
- Tool 선택 품질은 Gemini와 Tool description에 영향을 받는다.
- `get_sensor_history`는 context 크기를 줄이기 위해 기본 50개 window로 제한했다.
- 로컬 지식 문서는 제조사 정비 매뉴얼이 아닌 일반 데모 가이드다.
- 센서와 실제 설비 위치의 매핑이 없다.
- Backend streaming이 없어 Tool Trace는 응답 완료 후 순차 animation으로 표시한다.

MCP Session 지연이 문제가 되면 request-scoped Session 재사용을 검토한다. Tool 선택 품질은 실제 질문 평가셋으로 호출 정확도, 불필요 호출률, citation 일치율과 평균 단계 수를 측정해야 한다.

---

## 정리

Part 5의 핵심은 그래프에 `Agent`라는 이름을 붙인 것이 아니다.

```text
Before
질문 분류 → 코드가 정한 경로 → 답변

After
질문과 observation 확인
→ LLM이 다음 Tool 선택
→ 결과를 다시 관찰
→ 필요하면 반복
→ 충분하면 답변
```

동시에 자율성의 범위를 read-only 정보 수집으로 제한했다. MCP 데이터 경계, 2019년 historical timestamp, Risk Score 해석, citation, 명시적 Web 권한과 deterministic fallback은 그대로 유지한다.

## 참고 자료

- [LangChain Documentation](https://docs.langchain.com/)
- [LangGraph Documentation](https://docs.langchain.com/oss/python/langgraph/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
