---
title: "[Smart Factory MCP #4] 센서 상태와 점검 지식을 연결하기: RAG, LangChain, LangGraph, ChromaDB"
description: "TimescaleDB 센서 상태와 ChromaDB 점검 문서를 LangGraph로 라우팅하고 Flask 자연어 질의 API로 제공한 과정을 정리합니다."
tags:
  - Smart Factory
  - RAG
  - LangChain
  - LangGraph
  - ChromaDB
  - Flask
published: false
---

# [Smart Factory MCP #4] 센서 상태와 점검 지식을 연결하기: RAG, LangChain, LangGraph, ChromaDB

> Part 3에서 만든 센서 조회 API에 문서 검색과 질의 Workflow를 연결한다.  
> 오프라인으로도 재현 가능한 RAG 흐름을 만든 뒤 Gemini 답변 생성과 조건부 Tavily 검색을 연결했다.

## 들어가며

Part 3의 Flask와 MCP는 센서 상태를 정확히 조회할 수 있었다.

```text
센서 92
상태          DEGRADING
Risk Score    81.83
기준 시각      2019-07-30 20:42:00
```

하지만 이 결과만으로는 “무엇을 확인해야 하는가?”에 답할 수 없다. 반대로 정비 문서만 검색하면 실제 센서 상태와 관계없는 일반적인 설명만 반환할 수 있다.

Part 4에서는 두 종류의 데이터를 결합한다.

```text
정형 데이터    MCP Tool로 조회한 TimescaleDB 센서 상태
비정형 데이터  점검 가이드 Markdown
```

---

## 1. 전체 구조

```text
사용자 질문
    │
    ▼
Gemini Structured Output Router
    ├─ sensor    → MCP Client → MCP Tool → TimescaleDB
    ├─ knowledge → LangChain Retriever → ChromaDB
    └─ hybrid    → TimescaleDB + ChromaDB
                         │
                         ▼
                 Gemini 답변 생성
                 (실패 시 안전 Template)
                         │
                         ▼
                    Flask /api/ask
```

사용한 버전은 다음과 같다.

```text
LangChain        1.3.14
LangGraph        1.2.10
ChromaDB         1.5.9
langchain-chroma 1.1.0
langchain-google-genai 4.3.2
langchain-tavily 0.2.18
```

---

## 2. 지식 문서 준비

제조사 매뉴얼을 확보하지 못한 상태에서 출처가 불분명한 문서를 수집하지 않았다. 대신 파이프라인 검증 목적의 일반적인 문서 4개를 직접 작성했다.

```text
진동 이상 1차 점검
센서 데이터 품질 확인
Risk Score 해석 원칙
용접 생산라인 점검 관점
```

모든 문서에는 다음 한계를 명시했다.

- 제조사 매뉴얼을 대체하지 않는다.
- 현장 안전 절차를 우선한다.
- 모델 점수만으로 고장을 확정하지 않는다.
- 센서와 실제 설비 위치의 매핑이 필요하다.

Markdown의 `##` 제목을 기준으로 나누자 원본 4개가 20개 검색 Chunk가 됐다.

---

## 3. 모델 다운로드 없는 Embedding

일반적인 임베딩 모델을 사용하면 모델 파일과 API 키가 필요할 수 있다. 현재 Mac의 저장공간과 재현성을 고려해 Scikit-learn의 `HashingVectorizer`를 LangChain `Embeddings` 규격으로 감쌌다.

```python
class HashingEmbeddings(Embeddings):
    def __init__(self):
        self.vectorizer = HashingVectorizer(
            n_features=768,
            analyzer="char_wb",
            ngram_range=(2, 5),
            alternate_sign=False,
            norm="l2",
        )
```

이 방식은 상태를 학습하지 않아 같은 문장이 항상 같은 벡터가 된다. 한국어 단어의 조사 변화에도 일부 대응할 수 있도록 문자 n-gram을 사용했다.

다만 범용 임베딩 모델보다 의미 이해 능력이 제한되므로 실제 문서가 늘어나면 검색 평가 후 교체해야 한다.

---

## 4. ChromaDB 인덱싱

LangChain Chroma Vector Store에 문서와 메타데이터를 함께 저장한다.

```python
Document(
    page_content=chunk,
    metadata={
        "source": "vibration_triage.md",
        "title": "진동 상승 시 확인 순서",
        "chunk": 2,
    },
)
```

실행 명령:

```bash
python scripts/index_knowledge.py
```

결과:

```text
Collection     smart_factory_maintenance
원본 문서       4개
Chunk          20개
Vector 차원     768
```

---

## 5. Gemini Structured Output과 LangGraph 라우팅

초기에는 재현 가능한 규칙 기반 Router로 시작한 뒤, 자연스러운 표현을 처리하기 위해 Gemini Structured Output Router로 확장했다.

```json
{
  "route": "hybrid",
  "sensor_id": "92",
  "needs_web": false,
  "reason": "센서 상태와 점검 방법을 함께 요청함"
}
```

구조화 응답은 Pydantic Schema로 제한한다. Gemini 호출 또는 검증이 실패하면 기존 키워드 분류가 Fallback으로 동작한다. Tavily 실행은 모델이 임의로 결정하지 못하도록 사용자가 웹 검색을 명시했는지 코드에서 다시 확인한다.

| 질문 | Route |
|---|---|
| 센서 92 상태 알려줘 | `sensor` |
| 진동 상승 시 무엇을 점검해? | `knowledge` |
| 센서 92 상태와 점검 방법 알려줘 | `hybrid` |

LangGraph는 다음 순서를 실행한다.

```text
START
→ route
→ sensor 또는 knowledge
→ hybrid이면 두 노드 모두 실행
→ compose
→ END
```

센서 ID는 현재 모델에 포함된 `84`, `92`, `109`만 추출한다.

---

## 6. Gemini 답변 생성과 Tavily 검색

다음 질문을 실행했다.

```text
센서 92 상태와 점검 방법을 알려줘
```

Workflow는 MCP의 `get_sensor_status` Tool로 센서 92의 마지막 상태를 조회하고, ChromaDB에서 관련 점검 Chunk 3개를 가져왔다. LangGraph는 더 이상 Repository를 직접 호출하지 않는다.

```text
LangGraph Sensor Node
→ SensorMCPClient
→ MCP stdio Server
→ get_sensor_status
→ SensorRepository
→ TimescaleDB
```

센서 ID가 없는 전체 상태 질문은 하나의 MCP Session에서 `get_factory_summary`와 `get_abnormal_sensors`를 순서대로 호출한다. MCP 프로세스에는 DB 환경변수만 전달해 LLM과 검색 API 키가 불필요하게 노출되지 않게 했다.

응답에는 다음 정보가 함께 포함된다.

```text
기준 시각과 과거 데이터 여부
상태와 Risk Score
sample_count와 수집 간격
RMS와 Peak-to-Peak
검색된 점검 가이드
문서 출처
고장 확정 금지 문구
```

기본 답변 모델은 `gemini-2.5-flash`다. 모델에는 센서 DB 결과와 검색된 문서만 컨텍스트로 전달하고 다음 규칙을 강제했다.

- 2019년 저장 데이터를 실시간 상태로 표현하지 않는다.
- Risk Score를 고장 확률로 표현하지 않는다.
- 센서와 실제 부품의 매핑을 추측하지 않는다.
- 근거가 부족하면 부족하다고 밝힌다.
- 안전 절차와 제조사 매뉴얼을 우선한다.

답변이 내부 추론 토큰 때문에 잘리지 않도록 `gemini-2.5-flash`의 `thinking_budget`을 `0`, 최대 출력은 1,500토큰으로 설정했다. 그래도 응답 종료 이유가 `MAX_TOKENS`이면 잘린 문장을 보여주지 않고 완전한 안전 Template으로 전환한다.

Tavily는 모든 질문에 호출하지 않는다. 사용자가 `웹 검색`, `검색해`, `최신 자료`처럼 외부 검색을 명시한 경우에만 LangGraph의 `web` 노드를 실행한다.

```text
일반 질문       TimescaleDB + ChromaDB → Gemini
웹 검색 질문    TimescaleDB + ChromaDB + Tavily → Gemini
API 호출 실패   TimescaleDB + ChromaDB → 안전 Template
```

---

## 7. Flask 자연어 질의 API

Part 3 대시보드에 질문 입력창을 추가했다.

```http
POST /api/ask
Content-Type: application/json

{
  "question": "센서 92 상태와 점검 방법을 알려줘"
}
```

응답 구조:

```json
{
  "route": "hybrid",
  "sensor_id": "92",
  "answer": "...",
  "citations": [
    {"source": "vibration_triage.md", "chunk": 2}
  ],
  "retrieved_document_count": 3,
  "generation_mode": "google_gemini",
  "web_search_used": false,
  "web_result_count": 0
}
```

---

## 8. 검증

```bash
python scripts/part4_smoke_test.py
python scripts/part4_online_smoke_test.py
```

검증 결과:

```text
sensor Route       성공
knowledge Route    성공
hybrid Route       성공
ChromaDB 검색       성공
TimescaleDB 조회    성공
Flask /api/ask     HTTP 200
빈 질문             HTTP 400
Gemini 생성          성공
조건부 Tavily 검색    성공
웹 URL Citation      성공
```

---

## 9. 현재 한계와 다음 단계

현재 구현으로 RAG, LangChain, LangGraph와 Vector DB의 연결은 완료됐다. 하지만 운영 수준이라고 보기는 어렵다.

- 제조사 정비 매뉴얼이 아닌 데모 문서를 사용한다.
- Hashing Embedding의 검색 품질을 평가하지 않았다.
- 외부 LLM과 검색 API의 비용·지연·가용성에 영향을 받는다.
- 센서-설비 매핑과 작업 이력이 없다.
- 정답 데이터 기반의 Retrieval 평가가 없다.

다음 단계에서는 실제 사용 권한이 있는 문서를 확보하고 질문-정답 평가셋을 만든 뒤, Embedding과 Retriever의 `Precision@K`, `Recall@K`를 비교한다. LLM 답변도 출처 일치, 기준 시각 보존, 고장 확정 표현 여부를 별도로 평가해야 한다.

---

## 정리

```text
TimescaleDB 센서 상태
+ MCP Client / Tool
+ ChromaDB 점검 문서
+ LangChain Retriever
+ LangGraph Routing
+ Gemini 답변 생성
+ 조건부 Tavily 검색
→ Flask 자연어 질의 API
```

Part 4에서 중요한 것은 LLM에게 모든 판단을 맡기는 것이 아니었다. 정형 데이터, 로컬 문서와 외부 검색의 경계를 나누고, 어떤 정보가 어디에서 왔는지를 보존한 상태에서 LLM은 답변 표현만 담당하게 했다.

## 참고 자료

- [LangChain Documentation](https://docs.langchain.com/)
- [LangGraph Documentation](https://docs.langchain.com/oss/python/langgraph/)
- [Chroma Documentation](https://docs.trychroma.com/)
