# Part 4 — RAG + LangChain + LangGraph + ChromaDB

센서 `92`, `109`, `84`의 상태를 MCP Tool로 조회하고 점검 문서를 결합해 Gemini로 답변하는 자연어 질의 MVP다. 명시적인 외부 검색 요청에는 Tavily를 추가로 사용한다.

## 구현 상태

```text
RAG                  완료
LangChain Retriever  완료
Gemini LLM Routing   완료
MCP Sensor Client    완료
ChromaDB             완료
Flask /api/ask       완료
Gemini 답변 생성      완료
조건부 Tavily 검색    완료
```

다음 방식을 사용한다.

- Embedding: Scikit-learn `HashingVectorizer` 기반 로컬 768차원 벡터
- Vector DB: ChromaDB Persistent Client
- Retriever: LangChain
- Workflow: LangGraph + Gemini Structured Output Router
- 답변 생성: Gemini (`gemini-2.5-flash` 기본값)
- 외부 검색: 사용자가 웹 검색을 명시한 경우에만 Tavily
- 장애 대응: API 키가 없거나 호출이 실패하면 결정론적 Template
- Router 장애 대응: 기존 키워드 Router로 자동 전환

## 질의 경로

```text
센서 상태 질문 → MCP Client → TimescaleDB
점검 지식 질문 → ChromaDB
복합 질문       → TimescaleDB + ChromaDB
웹 검색 명시    → 기존 경로 + Tavily
```

## 문서

- [실행 Runbook](part4_runbook.md)
- [블로그 초안](smart_factory_mcp_blog_part4.md)
- [인덱스 결과](results/index_summary.json)

## 코드

- `rag/embeddings.py`
- `rag/vector_store.py`
- `rag/integrations.py`
- `rag/workflow.py`
- `mcp_server/client.py`
- `scripts/index_knowledge.py`
- `scripts/part4_smoke_test.py`
- `scripts/part4_mocked_integration_test.py`
- `scripts/part4_online_smoke_test.py`
- `knowledge/*.md`

## 주의

현재 지식 문서는 제조사 매뉴얼이 아니라 파이프라인 검증을 위한 일반 가이드다. 현장 정비 판단에는 실제 설비 매뉴얼, 센서-장비 매핑과 안전 절차가 추가로 필요하다.
