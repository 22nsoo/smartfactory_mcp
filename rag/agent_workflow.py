from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from mcp_server.client import SensorMCPClient
from rag.tools import build_agent_tools, serialize_document
from rag.vector_store import open_vector_store


WEB_TERMS = ("웹 검색", "인터넷 검색", "검색해", "검색해서", "최신 자료", "외부 자료")
SENSOR_PATTERN = re.compile(r"(?:센서|sensor)\s*#?\s*(84|92|109)\b", re.IGNORECASE)
SENSOR_TERMS = ("센서", "상태", "risk", "점수", "이상", "최근", "공장", "설비")
KNOWLEDGE_TERMS = ("점검", "조치", "정비", "원인", "방법", "매뉴얼", "가이드", "확인", "진동")
DEFAULT_MAX_AGENT_STEPS = 8
MCP_TOOL_NAMES = {
    "list_monitored_sensors",
    "get_model_summary",
    "get_sensor_status",
    "get_abnormal_sensors",
    "get_sensor_history",
    "get_anomaly_detail",
    "get_factory_summary",
}

SYSTEM_PROMPT = """당신은 스마트 팩토리 historical sensor data 분석 보조 Agent다.

목표:
- 사용자 질문을 해결하는 데 필요한 Tool만 선택한다.
- Tool 결과가 부족하면 이전 observation을 읽고 추가 Tool을 선택한다.
- 충분한 근거가 모이면 Tool 호출을 중지하고 한국어로 답변한다.

데이터 규칙:
- 센서 데이터는 2019년 historical dataset의 저장 데이터다.
- 마지막 저장값을 현재 또는 실시간 공장 상태라고 표현하지 않는다.
- Risk Score는 고장 확률이 아니라 비지도 모델의 이상 후보 점수다.
- Risk Score만으로 고장, 설비 정지 또는 부품 교체 필요성을 확정하지 않는다.
- 센서 ID를 실제 기계나 부품 위치로 임의 매핑하지 않는다.
- 지원 센서 ID를 추측하지 말고 필요하면 list_monitored_sensors를 호출한다.

Tool 사용 원칙:
- 특정 센서의 마지막 저장 상태에는 get_sensor_status를 우선 고려한다.
- 센서 상태나 수치를 묻는 질문은 추측으로 답하지 말고 반드시 적절한 MCP Tool 근거를 확보한다.
- 상태 변화나 추세가 필요할 때만 get_sensor_history를 추가로 고려한다.
- 특정 window의 상세 검토에는 get_anomaly_detail을 사용한다.
- 공장 전체 현황에는 get_factory_summary 또는 get_abnormal_sensors를 사용한다.
- 점검, 정비 또는 원인 관련 지식에는 search_maintenance_knowledge를 사용한다.
- search_web은 현재 요청에서 제공된 경우에만 호출할 수 있으며, 사용자가 명시적으로 외부 검색을 요청했다면 반드시 사용한다.
- 필요한 정보가 이미 충분하면 불필요한 Tool을 호출하지 않는다.

안전과 출처:
- 로컬 정비 문서는 파이프라인 검증용 일반 가이드이며 제조사 매뉴얼이 아니다.
- 점검 조언에는 현장 안전 절차와 제조사 매뉴얼을 우선해야 한다고 명시한다.
- 로컬 문서를 사용하면 source를, 웹 자료를 사용하면 URL을 답변 가까이에 표시한다.
- 근거가 부족하거나 Tool이 실패하면 그 한계를 명시한다.
"""


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    web_search_allowed: bool
    agent_step_count: int
    generation_mode: str
    generation_error: str


def explicit_web_request(question: str) -> bool:
    lower = question.lower()
    return any(term in lower for term in WEB_TERMS)


def classify_offline(question: str) -> tuple[str, str | None]:
    match = SENSOR_PATTERN.search(question)
    sensor_id = match.group(1) if match else None
    lower = question.lower()
    has_sensor = sensor_id is not None or any(term in lower for term in SENSOR_TERMS)
    has_knowledge = any(term in lower for term in KNOWLEDGE_TERMS)
    if has_sensor and has_knowledge:
        return "hybrid", sensor_id
    if has_sensor:
        return "sensor", sensor_id
    return "knowledge", sensor_id


def _safe_tool_error(error: Exception) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "tool_execution_failed",
            "error_type": type(error).__name__,
        },
        ensure_ascii=False,
    )


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return str(content).strip()


def _finish_reason(message: Any) -> str | None:
    metadata = getattr(message, "response_metadata", None) or {}
    reason = metadata.get("finish_reason") or metadata.get("finishReason")
    if reason is None:
        return None
    return str(getattr(reason, "name", None) or reason).upper()


def _tool_artifacts(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.artifact, dict):
            artifacts.append(message.artifact)
    return artifacts


def _collect_context(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    mcp_results: dict[str, list[dict[str, Any]]] = {}
    documents: list[dict[str, Any]] = []
    web_results: list[dict[str, Any]] = []
    web_error: str | None = None
    for artifact in artifacts:
        kind = artifact.get("kind")
        if kind == "mcp" and isinstance(artifact.get("data"), dict):
            name = str(artifact.get("tool"))
            mcp_results.setdefault(name, []).append(artifact["data"])
        elif kind == "knowledge":
            documents.extend(
                item for item in artifact.get("documents", []) if isinstance(item, dict)
            )
        elif kind == "web":
            web_results.extend(
                item for item in artifact.get("results", []) if isinstance(item, dict)
            )
            if artifact.get("error"):
                web_error = str(artifact["error"])
    return {
        "mcp_results": mcp_results,
        "documents": documents,
        "web_results": web_results,
        "web_error": web_error,
    }


def _template_answer(context: dict[str, Any], limit_reached: bool = False) -> str:
    sections: list[str] = []
    mcp_results = context.get("mcp_results", {})
    statuses = mcp_results.get("get_sensor_status", [])
    if statuses:
        data = statuses[-1]
        lines = [f"센서 {data.get('sensor_id')}의 마지막 저장 상태입니다."]
        if data.get("as_of"):
            lines.append(f"- 기준 시각: {data['as_of']}")
        lines.append(
            f"- 상태: {data.get('status', '확인 불가')} (Risk Score {data.get('risk_score', '확인 불가')})"
        )
        if data.get("sample_count") is not None:
            lines.append(
                f"- 단위/표본 수: {data.get('unit', '-')} / {data['sample_count']}개"
            )
        if data.get("rms") is not None:
            lines.append(
                f"- RMS: {data['rms']}, Peak-to-Peak: {data.get('peak_to_peak', '-')}"
            )
        sections.append("\n".join(lines))

    summaries = mcp_results.get("get_factory_summary", [])
    if summaries:
        summary = summaries[-1]
        sections.append(
            "\n".join(
                [
                    f"마지막 저장 시각 {summary.get('as_of')} 기준 공장 요약입니다.",
                    (
                        f"- NORMAL {summary.get('normal_count', 0)}, "
                        f"ATTENTION {summary.get('attention_count', 0)}, "
                        f"DEGRADING {summary.get('degrading_count', 0)}, "
                        f"WARNING {summary.get('warning_count', 0)}"
                    ),
                ]
            )
        )

    abnormal = mcp_results.get("get_abnormal_sensors", [])
    if abnormal and abnormal[-1].get("sensors"):
        sections.append(
            "이상 상태 센서: "
            + ", ".join(
                f"{item.get('sensor_id')}({item.get('status')}, {item.get('risk_score')})"
                for item in abnormal[-1]["sensors"]
            )
        )

    documents = context.get("documents", [])
    if documents:
        sections.append(
            "관련 점검 지식:\n"
            + "\n".join(
                f"- {item.get('excerpt', '')} [출처: {item.get('source')}]"
                for item in documents
            )
        )

    web_results = context.get("web_results", [])
    if web_results:
        sections.append(
            "요청한 외부 검색 자료:\n"
            + "\n".join(
                f"- {item.get('title')}: {item.get('content')} [URL: {item.get('url')}]"
                for item in web_results
            )
        )

    if not sections:
        sections.append("요청을 처리할 근거 데이터를 확보하지 못했습니다.")
    if limit_reached:
        sections.append("추가 조회 한도에 도달하여 현재 확보된 정보만으로 답변합니다.")
    sections.append(
        "주의: 이 결과는 2019년 공개 데이터의 비지도 이상 후보이며 실제 고장을 확정하지 않습니다. "
        "현장 안전 절차와 제조사 매뉴얼을 우선하고 자격을 갖춘 담당자가 확인해야 합니다."
    )
    return "\n\n".join(sections)


class ToolCallingSmartFactoryAgent:
    """Part 5 bounded read-only Tool-Calling Agent."""

    def __init__(
        self,
        sensor_client: SensorMCPClient,
        top_k: int = 3,
        llm: Any | None = None,
        web_search: Any | None = None,
        retriever: Any | None = None,
        max_steps: int | None = None,
    ):
        self.sensor_client = sensor_client
        self.llm = llm
        self.web_search = web_search
        self.max_steps = (
            int(os.getenv("AGENT_MAX_STEPS", str(DEFAULT_MAX_AGENT_STEPS)))
            if max_steps is None
            else max_steps
        )
        if not 1 <= self.max_steps <= 20:
            raise ValueError("max_steps must be between 1 and 20")

        if retriever is None:
            store = open_vector_store()
            if not store.get(limit=1).get("ids"):
                raise RuntimeError("Knowledge index is empty; run scripts/index_knowledge.py")
            retriever = store.as_retriever(search_kwargs={"k": top_k})
        self.retriever = retriever
        self.base_tools, self.web_tool = build_agent_tools(
            sensor_client, self.retriever, web_search
        )
        self.all_tools = [*self.base_tools]
        if self.web_tool is not None:
            self.all_tools.append(self.web_tool)
        self.graph = self._build_graph() if llm is not None else None

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("agent", self._agent_node)
        builder.add_node(
            "tools",
            ToolNode(self.all_tools, handle_tool_errors=_safe_tool_error),
        )
        builder.add_node("limit_fallback", self._limit_fallback)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges(
            "agent",
            self._should_continue,
            {"tools": "tools", "limit_fallback": "limit_fallback", "end": END},
        )
        builder.add_edge("tools", "agent")
        builder.add_edge("limit_fallback", END)
        return builder.compile()

    def _available_tools(self, state: AgentState) -> list[Any]:
        tools = [*self.base_tools]
        if state.get("web_search_allowed") and self.web_tool is not None:
            tools.append(self.web_tool)
        return tools

    def _agent_node(self, state: AgentState) -> AgentState:
        step_count = state.get("agent_step_count", 0) + 1
        try:
            bound_llm = self.llm.bind_tools(self._available_tools(state))
            message = bound_llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), *state.get("messages", [])]
            )
            if not isinstance(message, AIMessage):
                message = AIMessage(content=_message_text(message))
            finish_reason = _finish_reason(message)
            if finish_reason and "MAX_TOKENS" in finish_reason:
                raise RuntimeError("MAX_TOKENS")
            if not message.tool_calls and not _message_text(message):
                raise RuntimeError("empty_model_response")
            result: AgentState = {
                "messages": [message],
                "agent_step_count": step_count,
            }
            if not message.tool_calls:
                result["generation_mode"] = "google_gemini_tool_agent"
            return result
        except Exception as error:
            context = _collect_context(_tool_artifacts(state.get("messages", [])))
            return {
                "messages": [AIMessage(content=_template_answer(context))],
                "agent_step_count": step_count,
                "generation_mode": "deterministic_provider_fallback",
                "generation_error": str(error) if str(error) == "MAX_TOKENS" else type(error).__name__,
            }

    def _should_continue(self, state: AgentState) -> str:
        last_message = state.get("messages", [])[-1]
        tool_calls = getattr(last_message, "tool_calls", None) or []
        if not tool_calls:
            return "end"
        if state.get("agent_step_count", 0) >= self.max_steps:
            return "limit_fallback"
        return "tools"

    @staticmethod
    def _limit_fallback(state: AgentState) -> AgentState:
        context = _collect_context(_tool_artifacts(state.get("messages", [])))
        return {
            "messages": [AIMessage(content=_template_answer(context, limit_reached=True))],
            "generation_mode": "deterministic_provider_fallback",
            "generation_error": "agent_step_limit",
        }

    def _offline_context(self, question: str) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        route, sensor_id = classify_offline(question)
        artifacts: list[dict[str, Any]] = []
        web_error: str | None = None
        if route in {"sensor", "hybrid"}:
            if sensor_id:
                data = self.sensor_client.call_tool("get_sensor_status", {"sensor_id": sensor_id})
                artifacts.append({"kind": "mcp", "tool": "get_sensor_status", "data": data})
            else:
                summary = self.sensor_client.call_tool("get_factory_summary")
                abnormal = self.sensor_client.call_tool(
                    "get_abnormal_sensors", {"minimum_status": "DEGRADING", "limit": 20}
                )
                artifacts.extend(
                    [
                        {"kind": "mcp", "tool": "get_factory_summary", "data": summary},
                        {"kind": "mcp", "tool": "get_abnormal_sensors", "data": abnormal},
                    ]
                )
        if route in {"knowledge", "hybrid"}:
            documents = [serialize_document(item) for item in self.retriever.invoke(question)]
            artifacts.append(
                {
                    "kind": "knowledge",
                    "tool": "search_maintenance_knowledge",
                    "query": question,
                    "documents": documents,
                }
            )
        if explicit_web_request(question):
            if self.web_search is None:
                web_error = "Tavily search is not configured"
            else:
                try:
                    response = self.web_search.invoke({"query": question})
                    raw = response.get("results", []) if isinstance(response, dict) else response
                    results = []
                    if isinstance(raw, list):
                        results = [
                            {
                                "title": item.get("title") or item.get("url") or "웹 자료",
                                "url": item.get("url"),
                                "content": str(item.get("content") or "")[:700],
                            }
                            for item in raw
                            if isinstance(item, dict)
                        ]
                    artifacts.append(
                        {
                            "kind": "web",
                            "tool": "search_web",
                            "allowed": True,
                            "query": question,
                            "results": results,
                        }
                    )
                except Exception as error:
                    web_error = type(error).__name__
        context = _collect_context(artifacts)
        if web_error:
            context["web_error"] = web_error
        return context, artifacts, sensor_id

    @staticmethod
    def _trace_summary(tool_name: str, artifact: Any) -> str:
        if not isinstance(artifact, dict):
            return "Tool 실행 결과를 사용할 수 없습니다."
        if artifact.get("kind") == "mcp":
            data = artifact.get("data", {})
            if tool_name == "get_sensor_status":
                return (
                    f"{data.get('status', 'UNKNOWN')}, Risk Score "
                    f"{data.get('risk_score', '-')}, {data.get('as_of', '-')} 기준"
                )
            if tool_name == "get_sensor_history":
                return f"{data.get('returned_count', 0)}개 historical window 조회"
            if tool_name == "get_abnormal_sensors":
                return f"이상 상태 센서 {data.get('count', len(data.get('sensors', [])))}개 조회"
            if tool_name == "get_factory_summary":
                return f"모니터링 센서 {data.get('monitored_sensor_count', 0)}개 집계"
            if tool_name == "list_monitored_sensors":
                return f"모니터링 센서 {data.get('sensor_count', 0)}개 조회"
            if tool_name == "get_anomaly_detail":
                return f"{data.get('window_start', '-')} anomaly window 상세 조회"
            if tool_name == "get_model_summary":
                return f"Model run {data.get('model_run_id', '-')} metadata 조회"
        if artifact.get("kind") == "knowledge":
            return f"로컬 점검 문서 {len(artifact.get('documents', []))}개 검색"
        if artifact.get("kind") == "web":
            if not artifact.get("allowed", False):
                return "명시적 외부 검색 요청이 없어 실행하지 않음"
            return f"외부 자료 {len(artifact.get('results', []))}개 검색"
        return "Tool 실행 완료"

    @staticmethod
    def _tool_trace(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        responses = {
            message.tool_call_id: message
            for message in messages
            if isinstance(message, ToolMessage)
        }
        trace: list[dict[str, Any]] = []
        step = 0
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls:
                step += 1
                response = responses.get(call.get("id", ""))
                tool_name = str(call.get("name"))
                status = (
                    getattr(response, "status", "success")
                    if response is not None
                    else "not_executed"
                )
                trace.append(
                    {
                        "step": step,
                        "tool": tool_name,
                        "type": (
                            "mcp"
                            if tool_name in MCP_TOOL_NAMES
                            else "rag"
                            if tool_name == "search_maintenance_knowledge"
                            else "web"
                            if tool_name == "search_web"
                            else "tool"
                        ),
                        "arguments": call.get("args", {}),
                        "status": status,
                        "summary": (
                            ToolCallingSmartFactoryAgent._trace_summary(
                                tool_name, getattr(response, "artifact", None)
                            )
                            if status == "success"
                            else "Tool 실행 실패"
                            if status == "error"
                            else "최대 실행 단계로 인해 호출하지 않음"
                        ),
                    }
                )
        return trace

    @staticmethod
    def _response_from_context(
        question: str,
        answer: str,
        context: dict[str, Any],
        artifacts: list[dict[str, Any]],
        sensor_id: str | None,
        generation_mode: str,
        generation_error: str | None,
        agent_step_count: int,
        tool_trace: list[dict[str, Any]],
        agent_mode: str,
    ) -> dict[str, Any]:
        documents = context.get("documents", [])
        web_results = context.get("web_results", [])
        citations: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for item in documents:
            key = ("local_document", item.get("source"), item.get("chunk"))
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                {
                    "type": "local_document",
                    "source": item.get("source"),
                    "title": item.get("title"),
                    "chunk": item.get("chunk"),
                    "excerpt": item.get("excerpt"),
                }
            )
        for item in web_results:
            key = ("web", item.get("url"))
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                {
                    "type": "web",
                    "source": item.get("url"),
                    "title": item.get("title"),
                    "url": item.get("url"),
                }
            )

        mcp_results = context.get("mcp_results", {})
        sensor_data: dict[str, Any] | None = None
        if len(mcp_results) == 1 and len(next(iter(mcp_results.values()))) == 1:
            sensor_data = next(iter(mcp_results.values()))[0]
        elif mcp_results:
            sensor_data = {
                name: values[0] if len(values) == 1 else values
                for name, values in mcp_results.items()
            }

        if sensor_id is None:
            for artifact in artifacts:
                data = artifact.get("data") if artifact.get("kind") == "mcp" else None
                if isinstance(data, dict) and data.get("sensor_id") is not None:
                    sensor_id = str(data["sensor_id"])
                    break

        return {
            "question": question,
            "route": "agent",
            "sensor_id": sensor_id,
            "answer": answer,
            "citations": citations,
            "sensor_data": sensor_data,
            "sensor_data_source": "mcp" if mcp_results else None,
            "retrieved_document_count": len(documents),
            "web_search_used": any(item.get("kind") == "web" for item in artifacts),
            "web_result_count": len(web_results),
            "web_search_error": context.get("web_error"),
            "router_mode": "not_used",
            "router_reason": "LLM이 Tool 결과를 관찰하며 다음 행동을 선택했습니다.",
            "router_error": None,
            "generation_mode": generation_mode,
            "generation_error": generation_error,
            "agent_mode": agent_mode,
            "agent_step_count": agent_step_count,
            "tool_trace": tool_trace,
        }

    def _offline_ask(
        self,
        question: str,
        generation_mode: str = "deterministic_offline_fallback",
        generation_error: str | None = None,
        agent_mode: str = "deterministic_offline",
    ) -> dict[str, Any]:
        context, artifacts, sensor_id = self._offline_context(question)
        trace = []
        for index, artifact in enumerate(artifacts, start=1):
            tool_name = str(artifact.get("tool"))
            arguments: dict[str, Any] = {}
            if artifact.get("query"):
                arguments["query"] = artifact["query"]
            data = artifact.get("data")
            if isinstance(data, dict) and data.get("sensor_id") is not None:
                arguments["sensor_id"] = str(data["sensor_id"])
            trace.append(
                {
                    "step": index,
                    "tool": tool_name,
                    "type": (
                        "rag" if artifact.get("kind") == "knowledge" else artifact.get("kind")
                    ),
                    "arguments": arguments,
                    "status": "success",
                    "summary": self._trace_summary(tool_name, artifact),
                }
            )
        return self._response_from_context(
            question=question,
            answer=_template_answer(context),
            context=context,
            artifacts=artifacts,
            sensor_id=sensor_id,
            generation_mode=generation_mode,
            generation_error=generation_error,
            agent_step_count=0,
            tool_trace=trace,
            agent_mode=agent_mode,
        )

    def ask(self, question: str) -> dict[str, Any]:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question is required")
        if len(normalized) > 1000:
            raise ValueError("question must be 1000 characters or fewer")
        if self.graph is None:
            return self._offline_ask(normalized)

        result = self.graph.invoke(
            {
                "question": normalized,
                "messages": [HumanMessage(content=normalized)],
                "web_search_allowed": explicit_web_request(normalized),
                "agent_step_count": 0,
            },
            config={"recursion_limit": self.max_steps * 2 + 4},
        )
        messages = result.get("messages", [])
        artifacts = _tool_artifacts(messages)
        context = _collect_context(artifacts)
        tool_trace = self._tool_trace(messages)
        if any(
            item["tool"] == "search_web" and item["status"] == "error"
            for item in tool_trace
        ):
            context["web_error"] = "tool_execution_failed"
            artifacts.append(
                {
                    "kind": "web",
                    "tool": "search_web",
                    "allowed": True,
                    "results": [],
                    "error": "tool_execution_failed",
                }
            )
        generation_mode = result.get("generation_mode", "google_gemini_tool_agent")
        generation_error = result.get("generation_error")
        if generation_mode == "deterministic_provider_fallback" and not artifacts:
            fallback = self._offline_ask(
                normalized,
                generation_mode=generation_mode,
                generation_error=generation_error,
                agent_mode="tool_calling_fallback",
            )
            fallback["agent_step_count"] = result.get("agent_step_count", 1)
            return fallback

        answer = next(
            (
                _message_text(message)
                for message in reversed(messages)
                if isinstance(message, AIMessage) and _message_text(message)
            ),
            _template_answer(context),
        )
        match = SENSOR_PATTERN.search(normalized)
        return self._response_from_context(
            question=normalized,
            answer=answer,
            context=context,
            artifacts=artifacts,
            sensor_id=match.group(1) if match else None,
            generation_mode=generation_mode,
            generation_error=generation_error,
            agent_step_count=result.get("agent_step_count", 0),
            tool_trace=tool_trace,
            agent_mode="tool_calling",
        )


def build_tool_calling_agent() -> ToolCallingSmartFactoryAgent:
    from rag.integrations import build_google_llm, build_tavily_search

    return ToolCallingSmartFactoryAgent(
        SensorMCPClient(),
        llm=build_google_llm(),
        web_search=build_tavily_search(),
    )
