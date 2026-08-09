from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from mcp_server.client import SensorMCPClient
from rag.vector_store import open_vector_store


Route = Literal["sensor", "knowledge", "hybrid"]
SENSOR_PATTERN = re.compile(r"(?:센서|sensor)\s*#?\s*(84|92|109)\b", re.IGNORECASE)
SENSOR_TERMS = ("센서", "상태", "risk", "점수", "이상", "최근", "공장", "설비")
KNOWLEDGE_TERMS = ("점검", "조치", "정비", "원인", "방법", "매뉴얼", "가이드", "확인", "진동")
WEB_TERMS = ("웹 검색", "인터넷 검색", "검색해", "검색해서", "최신 자료", "외부 자료")
SUPPORTED_SENSOR_IDS = {"84", "92", "109"}


class RouteDecision(BaseModel):
    route: Route = Field(description="질문 처리 경로")
    sensor_id: Literal["84", "92", "109"] | None = Field(
        default=None,
        description="질문에 명시된 지원 센서 ID",
    )
    needs_web: bool = Field(
        default=False,
        description="사용자가 외부 웹 검색을 명시적으로 요청했는지 여부",
    )
    reason: str = Field(description="분류 근거를 설명하는 짧은 한국어 문장")


class AgentState(TypedDict, total=False):
    question: str
    route: Route
    sensor_id: str | None
    sensor_data: dict[str, Any]
    documents: list[Document]
    needs_web: bool
    web_results: list[dict[str, Any]]
    web_error: str
    router_mode: str
    router_reason: str
    router_error: str
    answer: str
    citations: list[dict[str, Any]]
    generation_mode: str
    generation_error: str


def classify_question(question: str) -> tuple[Route, str | None]:
    normalized = question.strip()
    match = SENSOR_PATTERN.search(normalized)
    sensor_id = match.group(1) if match else None
    lower = normalized.lower()
    has_sensor = sensor_id is not None or any(term in lower for term in SENSOR_TERMS)
    has_knowledge = any(term in lower for term in KNOWLEDGE_TERMS)
    if has_sensor and has_knowledge:
        return "hybrid", sensor_id
    if has_sensor:
        return "sensor", sensor_id
    return "knowledge", sensor_id


def document_excerpt(document: Document, maximum: int = 360) -> str:
    text = " ".join(line.strip() for line in document.page_content.splitlines() if line.strip())
    return text if len(text) <= maximum else text[: maximum - 1].rstrip() + "…"


class SmartFactoryAgent:
    def __init__(
        self,
        sensor_client: SensorMCPClient,
        top_k: int = 3,
        llm: Any | None = None,
        web_search: Any | None = None,
    ):
        self.sensor_client = sensor_client
        self.llm = llm
        self.web_search = web_search
        self.structured_router = None
        if llm is not None and hasattr(llm, "with_structured_output"):
            self.structured_router = llm.with_structured_output(RouteDecision)
        self.store = open_vector_store()
        if not self.store.get(limit=1).get("ids"):
            raise RuntimeError("Knowledge index is empty; run scripts/index_knowledge.py")
        self.retriever = self.store.as_retriever(search_kwargs={"k": top_k})
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("route", self._route)
        builder.add_node("sensor", self._sensor)
        builder.add_node("knowledge", self._knowledge)
        builder.add_node("web", self._web)
        builder.add_node("compose", self._compose)
        builder.add_edge(START, "route")
        builder.add_conditional_edges(
            "route",
            lambda state: state["route"],
            {"sensor": "sensor", "knowledge": "knowledge", "hybrid": "sensor"},
        )
        builder.add_conditional_edges(
            "sensor",
            lambda state: (
                "knowledge"
                if state["route"] == "hybrid"
                else "web" if state.get("needs_web") else "compose"
            ),
            {"knowledge": "knowledge", "web": "web", "compose": "compose"},
        )
        builder.add_conditional_edges(
            "knowledge",
            lambda state: "web" if state.get("needs_web") else "compose",
            {"web": "web", "compose": "compose"},
        )
        builder.add_edge("web", "compose")
        builder.add_edge("compose", END)
        return builder.compile()

    def _route(self, state: AgentState) -> AgentState:
        question = state["question"]
        explicit_web_request = any(term in question.lower() for term in WEB_TERMS)
        fallback_route, fallback_sensor_id = classify_question(question)
        if self.structured_router is None:
            return {
                "route": fallback_route,
                "sensor_id": fallback_sensor_id,
                "needs_web": explicit_web_request,
                "router_mode": "deterministic_fallback",
                "router_reason": "Gemini Router가 없어 키워드 규칙을 사용했습니다.",
            }

        prompt = f"""다음 질문을 스마트 팩토리 조회 경로로 분류하세요.

경로 정의:
- sensor: 센서 상태, 이상 점수, 최근 저장값 또는 전체 공장 상태 조회
- knowledge: 점검 방법, 원인, 정비 지식 또는 일반 설명 검색
- hybrid: 센서 상태 조회와 점검 지식 검색이 모두 필요한 질문

판단 우선순위:
1. 상태·점수·이상 조회와 점검·조치·원인 설명을 함께 요구하면 반드시 hybrid
2. 상태나 수치 조회만 요구하면 sensor
3. 점검·정비 지식만 요구하면 knowledge

예시:
- "센서 92 상태 알려줘" → sensor
- "진동 상승 시 무엇을 점검해?" → knowledge
- "센서 92 상태와 점검 방법을 함께 알려줘" → hybrid
- "이상 있는 센서와 필요한 조치를 알려줘" → hybrid

제약:
- 지원 센서 ID는 84, 92, 109뿐입니다.
- 질문에 지원 센서 ID가 명시되지 않았다면 sensor_id는 null입니다.
- needs_web은 사용자가 웹/인터넷/외부/최신 자료 검색을 명시적으로 요청한 경우만 true입니다.
- 센서 ID나 실제 장비 정보를 추측하지 마세요.

질문: {question}
"""
        try:
            decision = self.structured_router.invoke(prompt)
            if isinstance(decision, dict):
                decision = RouteDecision.model_validate(decision)
            sensor_id = decision.sensor_id
            if sensor_id not in SUPPORTED_SENSOR_IDS:
                sensor_id = None
            return {
                "route": decision.route,
                "sensor_id": sensor_id,
                # External search is a deterministic consent/cost gate.
                "needs_web": explicit_web_request,
                "router_mode": "google_gemini_structured",
                "router_reason": decision.reason,
            }
        except Exception as error:
            return {
                "route": fallback_route,
                "sensor_id": fallback_sensor_id,
                "needs_web": explicit_web_request,
                "router_mode": "deterministic_fallback",
                "router_reason": "Gemini Router 실패로 키워드 규칙을 사용했습니다.",
                "router_error": type(error).__name__,
            }

    def _sensor(self, state: AgentState) -> AgentState:
        sensor_id = state.get("sensor_id")
        if sensor_id:
            data = self.sensor_client.get_sensor_status(sensor_id)
        else:
            data = self.sensor_client.get_factory_overview()
        return {"sensor_data": data}

    def _knowledge(self, state: AgentState) -> AgentState:
        documents = self.retriever.invoke(state["question"])
        return {"documents": documents}

    def _web(self, state: AgentState) -> AgentState:
        if self.web_search is None:
            return {"web_results": [], "web_error": "Tavily search is not configured"}
        try:
            response = self.web_search.invoke({"query": state["question"]})
            raw_results = response.get("results", []) if isinstance(response, dict) else response
            results = []
            for item in raw_results if isinstance(raw_results, list) else []:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "title": item.get("title") or item.get("url") or "웹 자료",
                        "url": item.get("url"),
                        "content": str(item.get("content") or "")[:700],
                    }
                )
            return {"web_results": results}
        except Exception as error:  # External search must not break local RAG.
            return {"web_results": [], "web_error": type(error).__name__}

    @staticmethod
    def _template_answer(state: AgentState) -> str:
        sections: list[str] = []
        data = state.get("sensor_data")
        if data:
            if "sensor_id" in data:
                sections.append(
                    "\n".join(
                        [
                            f"센서 {data['sensor_id']}의 마지막 저장 상태입니다.",
                            f"- 기준 시각: {data['as_of']}",
                            f"- 상태: {data['status']} (Risk Score {data['risk_score']:.2f})",
                            f"- 단위/표본 수: {data['unit']} / {data['sample_count']}개",
                            f"- RMS: {data['rms']:.3f}, Peak-to-Peak: {data['peak_to_peak']:.3f}",
                            f"- 직전 수집 간격: {data['gap_minutes']:.1f}분",
                            f"- 3-Sigma 탐지 Feature: {', '.join(data['sigma_detected_features']) or '없음'}",
                        ]
                    )
                )
            else:
                summary = data["factory_summary"]
                abnormal = data["abnormal_sensors"]["sensors"]
                lines = [
                    f"마지막 저장 시각 {summary['as_of']} 기준 센서 {summary['monitored_sensor_count']}개입니다.",
                    (
                        f"- NORMAL {summary['normal_count']}, ATTENTION {summary['attention_count']}, "
                        f"DEGRADING {summary['degrading_count']}, WARNING {summary['warning_count']}"
                    ),
                ]
                if abnormal:
                    lines.append(
                        "- 이상 상태 센서: "
                        + ", ".join(
                            f"{item['sensor_id']}({item['status']}, {item['risk_score']:.2f})"
                            for item in abnormal
                        )
                    )
                sections.append("\n".join(lines))

        documents = state.get("documents", [])
        if documents:
            guidance = ["관련 점검 지식:"]
            guidance.extend(
                f"- {document_excerpt(document)} [출처: {document.metadata.get('source')}]"
                for document in documents
            )
            sections.append("\n".join(guidance))

        web_results = state.get("web_results", [])
        if web_results:
            web_lines = ["요청한 외부 검색 자료:"]
            web_lines.extend(
                f"- {item['title']}: {item['content']} [URL: {item['url']}]"
                for item in web_results
            )
            sections.append("\n".join(web_lines))

        sections.append(
            "주의: 이 결과는 2019년 공개 데이터의 비지도 이상 후보이며 실제 고장을 확정하지 않습니다. "
            "현장 안전 절차와 제조사 매뉴얼을 우선하고 자격을 갖춘 담당자가 확인해야 합니다."
        )
        return "\n\n".join(sections)

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(parts).strip()
        return str(content).strip()

    @staticmethod
    def _finish_reason(message: Any) -> str | None:
        metadata = getattr(message, "response_metadata", None) or {}
        reason = metadata.get("finish_reason") or metadata.get("finishReason")
        if reason is None:
            return None
        name = getattr(reason, "name", None)
        return str(name or reason).upper()

    def _generate_answer(self, state: AgentState) -> tuple[str, str, str | None]:
        fallback = self._template_answer(state)
        if self.llm is None:
            return fallback, "deterministic_offline_template", None

        documents = [
            {
                "source": document.metadata.get("source"),
                "title": document.metadata.get("title"),
                "excerpt": document_excerpt(document, maximum=700),
            }
            for document in state.get("documents", [])
        ]
        context = {
            "sensor_data": state.get("sensor_data"),
            "local_documents": documents,
            "web_results": state.get("web_results", []),
        }
        prompt = f"""당신은 스마트 팩토리 센서 분석 보조자입니다.
아래 제공된 컨텍스트만 근거로 사용자의 질문에 한국어로 간결하게 답하세요.

필수 규칙:
- 센서 데이터는 2019년 공개 데이터의 마지막 저장값이며 실시간이라고 표현하지 마세요.
- Risk Score는 고장 확률이 아니고 비지도 모델의 이상 후보 점수입니다.
- 센서 ID와 실제 설비 부품의 매핑을 추측하지 마세요.
- 컨텍스트가 부족하면 부족하다고 명시하세요.
- 점검 조언에는 현장 안전 절차와 제조사 매뉴얼을 우선하라는 문장을 포함하세요.
- 외부 검색 자료를 사용했다면 URL을 답변 가까이에 표시하세요.

사용자 질문:
{state['question']}

컨텍스트:
{json.dumps(context, ensure_ascii=False, default=str)}
"""
        try:
            message = self.llm.invoke(prompt)
            finish_reason = self._finish_reason(message)
            if finish_reason and "MAX_TOKENS" in finish_reason:
                return fallback, "deterministic_fallback", "MAX_TOKENS"
            answer = self._message_text(message)
            if not answer:
                raise RuntimeError("empty model response")
            return answer, "google_gemini", None
        except Exception as error:  # Preserve service availability on provider failure.
            return fallback, "deterministic_fallback", type(error).__name__

    def _compose(self, state: AgentState) -> AgentState:
        documents = state.get("documents", [])
        citations = [
            {
                "type": "local_document",
                "source": document.metadata.get("source"),
                "title": document.metadata.get("title"),
                "chunk": document.metadata.get("chunk"),
            }
            for document in documents
        ]
        citations.extend(
            {
                "type": "web",
                "source": item.get("url"),
                "title": item.get("title"),
                "url": item.get("url"),
            }
            for item in state.get("web_results", [])
        )
        answer, mode, error = self._generate_answer(state)
        result: AgentState = {
            "answer": answer,
            "citations": citations,
            "generation_mode": mode,
        }
        if error:
            result["generation_error"] = error
        return result

    def ask(self, question: str) -> dict[str, Any]:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question is required")
        if len(normalized) > 1000:
            raise ValueError("question must be 1000 characters or fewer")
        result = self.graph.invoke({"question": normalized})
        return {
            "question": normalized,
            "route": result["route"],
            "sensor_id": result.get("sensor_id"),
            "answer": result["answer"],
            "citations": result.get("citations", []),
            "sensor_data": result.get("sensor_data"),
            "sensor_data_source": "mcp" if result.get("sensor_data") else None,
            "retrieved_document_count": len(result.get("documents", [])),
            "web_search_used": bool(result.get("web_results")),
            "web_result_count": len(result.get("web_results", [])),
            "web_search_error": result.get("web_error"),
            "router_mode": result.get("router_mode", "deterministic_fallback"),
            "router_reason": result.get("router_reason"),
            "router_error": result.get("router_error"),
            "generation_mode": result.get("generation_mode", "deterministic_offline_template"),
            "generation_error": result.get("generation_error"),
        }


def build_agent() -> SmartFactoryAgent:
    from rag.integrations import build_google_llm, build_tavily_search

    return SmartFactoryAgent(
        SensorMCPClient(),
        llm=build_google_llm(),
        web_search=build_tavily_search(),
    )
