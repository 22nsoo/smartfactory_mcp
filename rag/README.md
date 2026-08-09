# RAG와 Tool-Calling Agent

로컬 점검 문서 검색, Gemini 연동, 조건부 Tavily 검색과 LangGraph 실행 흐름을 담당합니다.

## 파일

| 파일 | 역할 |
|---|---|
| `embeddings.py` | 다운로드 없는 `HashingVectorizer` 기반 768차원 Embedding |
| `vector_store.py` | Markdown chunking, ChromaDB indexing과 store open |
| `integrations.py` | Gemini와 Tavily client 생성 |
| `workflow.py` | Part 4의 고정 `sensor / knowledge / hybrid` 기준선 |
| `tools.py` | MCP·RAG·Web 기능을 LangChain Tool로 wrapping |
| `agent_workflow.py` | Part 5 message 기반 `agent ↔ tools` 반복 실행 |

## Part 5 Agent Loop

```text
START
  ↓
agent ── final answer ──→ END
  │
  ├─ tool_calls → ToolNode → ToolMessage → agent
  └─ max steps  → deterministic fallback → END
```

Agent에 노출되는 기능은 MCP Tool 7개, `search_maintenance_knowledge`, 조건부 `search_web`입니다. Web Tool은 사용자가 외부 검색을 명시한 요청에만 bind하며 Tool 내부에서도 권한을 다시 확인합니다.

## 지식 인덱스

```bash
python scripts/index_knowledge.py
```

입력은 [`knowledge/`](../knowledge/README.md), 생성되는 ChromaDB 파일은 `data/vector_db/`에 저장되며 Git에서 제외됩니다.

## Fallback

```text
google_gemini_tool_agent          Gemini Tool-Calling 정상 실행
deterministic_offline_fallback    API 키 없이 로컬 조회와 Template 실행
deterministic_provider_fallback   Provider 오류 또는 최대 단계 도달
```

## 테스트

```bash
python scripts/part4_smoke_test.py
python scripts/part4_mocked_integration_test.py
python scripts/part5_agent_mocked_test.py
python scripts/part5_agent_smoke_test.py
```

실제 외부 API 테스트는 데이터 반출 정책을 확인한 후 실행합니다. 자세한 내용은 [Part 4](../docs/part4/README.md)와 [Part 5](../docs/part5/README.md)를 참고합니다.
