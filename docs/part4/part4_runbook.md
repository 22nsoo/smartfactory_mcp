# Part 4 실행 가이드

## 1. 환경 준비

```bash
cd /Users/insu/Desktop/smartfactory_mcp
source .venv/bin/activate
python -m pip install -r requirements-part4.txt
docker compose up -d
```

`.env`에 API 키와 선택 설정을 추가한다. 변수명과 `=` 사이에는 공백을 넣지 않는다.

```dotenv
GOOGLE_API_KEY=<Google AI Studio API 키>
GOOGLE_MODEL=gemini-2.5-flash
GOOGLE_MAX_TOKENS=1500
GOOGLE_THINKING_BUDGET=0
TAVILY_API_KEY=<Tavily API 키>
TAVILY_MAX_RESULTS=3
```

## 2. 지식 문서

현재 데모 문서는 `knowledge/`에 있다.

```text
data_quality_checks.md
risk_score_interpretation.md
vibration_triage.md
welding_line_inspection.md
```

실제 프로젝트에서는 제조사 매뉴얼, SOP, 예방정비 기준과 고장 사례를 검토한 뒤 이 디렉터리에 추가한다. 비밀번호, 개인정보와 배포 권한이 없는 문서는 넣지 않는다.

## 3. ChromaDB 인덱싱

```bash
python scripts/index_knowledge.py
```

현재 결과:

```text
원본 문서       4개
검색 Chunk      20개
Vector 차원     768
저장 위치       data/vector_db
```

`data/vector_db`는 생성 데이터이므로 Git에서 제외한다. 문서가 변경되면 인덱싱 명령을 다시 실행한다. ID는 문서 내용의 SHA-256으로 결정되므로 동일 Chunk는 중복 생성되지 않는다.

## 4. LangGraph 경로

| Route | 질문 예시 | 조회 대상 |
|---|---|---|
| `sensor` | 센서 92 상태 알려줘 | MCP `get_sensor_status` → TimescaleDB |
| `knowledge` | 진동 상승 시 무엇을 점검해? | ChromaDB |
| `hybrid` | 센서 92 상태와 점검 방법 알려줘 | MCP Tool + ChromaDB |

`웹 검색`, `검색해`, `최신 자료`처럼 외부 검색을 명시한 질문에만 Tavily 노드를 추가 실행한다. 일반 센서·점검 질문은 TimescaleDB와 ChromaDB만 사용한다.

질문은 Gemini Structured Output Router가 `route`, `sensor_id`, `needs_web`, `reason` 형태로 분류한다. 지원 센서 ID는 `84`, `92`, `109`로 제한하며, Gemini 호출이나 구조화 출력이 실패하면 기존 키워드 Router로 자동 전환한다. 웹 검색 실행 여부는 모델 판단만 신뢰하지 않고 사용자의 명시적 검색 표현을 코드에서 다시 검증한다.

LangGraph의 Sensor Node는 Repository를 직접 호출하지 않는다. `SensorMCPClient`가 stdio로 `mcp_server.server`를 실행하고 구조화된 Tool 결과를 받는다.

```text
sensor_id 있음  → MCP get_sensor_status
sensor_id 없음  → MCP get_factory_summary + get_abnormal_sensors
```

Flask의 기존 REST 엔드포인트는 Repository를 직접 사용하지만, `/api/ask`의 Agent 센서 조회는 MCP 경로를 사용한다. MCP 하위 프로세스에는 PostgreSQL 설정만 전달하며 Gemini와 Tavily API 키는 전달하지 않는다.

## 5. 종단 간 테스트

```bash
python scripts/part4_smoke_test.py
```

위 명령은 비용과 네트워크에 영향받지 않는 오프라인 회귀 테스트다. 실제 Gemini와 Tavily 연결은 다음 한 번의 복합 요청으로 확인한다.

외부 전송 없이 Gemini·Tavily 노드의 분기와 응답 구조만 검증하려면 다음 명령을 사용한다.

```bash
python scripts/part4_mocked_integration_test.py
```

실제 종단 간 테스트는 센서 상태와 검색 문서 일부를 Google 및 Tavily API로 전송한다. 데이터 반출 정책을 확인하고 허용되는 환경에서만 실행한다.

```bash
python scripts/part4_online_smoke_test.py
```

확인 항목:

- 세 경로가 예상대로 분기된다.
- 지식 질문에서 한 개 이상의 Chunk가 검색된다.
- 센서 92 상태가 TimescaleDB에서 조회된다.
- Flask `POST /api/ask`가 복합 답변을 반환한다.
- 빈 질문은 HTTP 400을 반환한다.

## 6. Flask 실행

```bash
python -m web_app.app
```

브라우저:

```text
http://127.0.0.1:5000/
```

REST 요청:

```bash
curl -X POST http://127.0.0.1:5000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"센서 92 상태와 점검 방법을 알려줘"}'
```

응답 핵심 필드:

```text
route
sensor_id
answer
citations
sensor_data
sensor_data_source
retrieved_document_count
generation_mode
router_mode
router_reason
web_search_used
web_result_count
```

## 7. 현재 생성 방식과 Fallback

`generation_mode`는 실행 상태에 따라 다음 값 중 하나다.

```text
google_gemini                   Gemini 정상 생성
deterministic_offline_template API 키 없이 로컬 Template 사용
deterministic_fallback         외부 호출 실패 후 로컬 Template 사용
```

기본 모델에서는 답변에 사용할 출력 공간을 확보하기 위해 내부 추론 예산을 `0`으로 설정한다. 모델이 `MAX_TOKENS`로 종료되면 중간에 잘린 문장을 반환하지 않고 완전한 로컬 Template으로 전환한다.

Gemini Prompt에는 센서 DB 결과, 로컬 검색 문서와 선택적으로 Tavily 결과만 전달한다. 다음 제한을 항상 적용한다.

- 검색 문서에 없는 사실을 생성하지 않도록 Prompt 제한
- `as_of`와 과거 데이터 여부 강제 표시
- 문서 출처 반환
- 고장 확정 표현 금지
- 센서와 실제 설비 부품 매핑 추측 금지

외부 API 장애가 나도 센서 조회와 로컬 RAG 답변은 Template으로 계속 동작한다.

## 8. 안전 및 한계

- 현재 문서는 제조사 매뉴얼이 아니다.
- Risk Score는 고장 확률이나 잔여 수명이 아니다.
- 설비 정지와 부품 교체를 자동 결정하지 않는다.
- 현장 안전 절차와 자격을 갖춘 담당자의 판단이 우선이다.
- 센서와 실제 설비 위치의 매핑이 없어 구체 부품을 확정할 수 없다.
