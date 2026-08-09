from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.documents import Document
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from mcp_server.client import SensorMCPClient


def _json_content(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _mcp_result(name: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    artifact = {"kind": "mcp", "tool": name, "data": payload}
    return _json_content(payload), artifact


def serialize_document(document: Document, maximum: int = 900) -> dict[str, Any]:
    text = " ".join(
        line.strip() for line in document.page_content.splitlines() if line.strip()
    )
    excerpt = text if len(text) <= maximum else text[: maximum - 1].rstrip() + "…"
    return {
        "source": document.metadata.get("source"),
        "title": document.metadata.get("title"),
        "chunk": document.metadata.get("chunk"),
        "excerpt": excerpt,
    }


def build_mcp_tools(sensor_client: SensorMCPClient) -> list[BaseTool]:
    @tool("list_monitored_sensors", response_format="content_and_artifact")
    def list_monitored_sensors() -> tuple[str, dict[str, Any]]:
        """프로젝트에서 모니터링하는 센서와 각 센서의 마지막 저장 상태를 조회한다. 사용 가능한 센서 ID를 모를 때 사용한다. 결과는 2019년 historical dataset이며 실시간 값이 아니다."""
        return _mcp_result(
            "list_monitored_sensors",
            sensor_client.call_tool("list_monitored_sensors"),
        )

    @tool("get_model_summary", response_format="content_and_artifact")
    def get_model_summary() -> tuple[str, dict[str, Any]]:
        """현재 anomaly model의 metadata, feature 목록, 결과 수와 historical data 범위를 조회한다. 센서 상태가 아니라 모델이나 데이터 범위 질문에 사용한다."""
        return _mcp_result(
            "get_model_summary",
            sensor_client.call_tool("get_model_summary"),
        )

    @tool("get_sensor_status", response_format="content_and_artifact")
    def get_sensor_status(sensor_id: str) -> tuple[str, dict[str, Any]]:
        """특정 센서의 마지막 저장 anomaly 상태와 주요 feature를 조회한다. 센서 상태를 처음 확인할 때 우선 사용한다. 지원 센서는 84, 92, 109이며 결과는 실시간 값이 아니다."""
        return _mcp_result(
            "get_sensor_status",
            sensor_client.call_tool("get_sensor_status", {"sensor_id": sensor_id}),
        )

    @tool("get_abnormal_sensors", response_format="content_and_artifact")
    def get_abnormal_sensors(
        minimum_status: str = "DEGRADING", limit: int = 20
    ) -> tuple[str, dict[str, Any]]:
        """마지막 저장 상태가 지정한 minimum status 이상인 센서를 조회한다. 공장 전체에서 어떤 센서가 이상 상태인지 찾을 때 사용한다."""
        return _mcp_result(
            "get_abnormal_sensors",
            sensor_client.call_tool(
                "get_abnormal_sensors",
                {"minimum_status": minimum_status, "limit": limit},
            ),
        )

    @tool("get_sensor_history", response_format="content_and_artifact")
    def get_sensor_history(
        sensor_id: str, hours: int = 24, limit: int = 50
    ) -> tuple[str, dict[str, Any]]:
        """특정 센서의 마지막 historical timestamp 이전 기록을 조회한다. 마지막 상태만으로 추세를 판단하기 어려울 때 상태, Risk Score와 feature 변화를 확인하는 데 사용한다. 응답 크기를 위해 기본 50개 window를 반환한다."""
        return _mcp_result(
            "get_sensor_history",
            sensor_client.call_tool(
                "get_sensor_history",
                {"sensor_id": sensor_id, "hours": hours, "limit": limit},
            ),
        )

    @tool("get_anomaly_detail", response_format="content_and_artifact")
    def get_anomaly_detail(
        sensor_id: str, window_start: str
    ) -> tuple[str, dict[str, Any]]:
        """특정 센서의 특정 anomaly window에 대한 model score, 3-Sigma feature, window feature와 이전 data gap을 조회한다. window_start는 다른 MCP Tool 결과의 정확한 ISO 시각을 사용한다."""
        return _mcp_result(
            "get_anomaly_detail",
            sensor_client.call_tool(
                "get_anomaly_detail",
                {"sensor_id": sensor_id, "window_start": window_start},
            ),
        )

    @tool("get_factory_summary", response_format="content_and_artifact")
    def get_factory_summary() -> tuple[str, dict[str, Any]]:
        """모든 monitored sensor의 마지막 저장 상태를 집계한다. 공장 전체 현황이나 상태별 센서 수 질문에 사용한다."""
        return _mcp_result(
            "get_factory_summary",
            sensor_client.call_tool("get_factory_summary"),
        )

    return [
        list_monitored_sensors,
        get_model_summary,
        get_sensor_status,
        get_abnormal_sensors,
        get_sensor_history,
        get_anomaly_detail,
        get_factory_summary,
    ]


def build_knowledge_tool(retriever: Any) -> BaseTool:
    @tool("search_maintenance_knowledge", response_format="content_and_artifact")
    def search_maintenance_knowledge(query: str) -> tuple[str, dict[str, Any]]:
        """로컬 ChromaDB에서 설비 점검, 정비, 센서 데이터 품질과 Risk Score 해석 지식을 검색한다. 문서는 일반 데모 가이드이며 제조사 매뉴얼을 대체하지 않는다."""
        documents = retriever.invoke(query)
        serialized = [serialize_document(document) for document in documents]
        payload = {"query": query, "documents": serialized}
        artifact = {"kind": "knowledge", "tool": "search_maintenance_knowledge", **payload}
        return _json_content(payload), artifact

    return search_maintenance_knowledge


def _normalize_web_results(response: Any) -> list[dict[str, Any]]:
    raw_results = response.get("results", []) if isinstance(response, dict) else response
    results: list[dict[str, Any]] = []
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
    return results


def build_web_tool(web_search: Any) -> BaseTool:
    @tool("search_web", response_format="content_and_artifact")
    def search_web(
        query: str,
        web_search_allowed: Annotated[bool, InjectedState("web_search_allowed")],
    ) -> tuple[str, dict[str, Any]]:
        """사용자가 웹 검색, 최신 자료 또는 외부 자료 검색을 명시적으로 요청한 경우에만 Tavily에서 외부 자료를 검색한다."""
        if not web_search_allowed:
            payload = {
                "allowed": False,
                "results": [],
                "error": "external_web_search_not_authorized",
            }
            return _json_content(payload), {
                "kind": "web",
                "tool": "search_web",
                **payload,
            }
        response = web_search.invoke({"query": query})
        results = _normalize_web_results(response)
        payload = {"allowed": True, "query": query, "results": results}
        return _json_content(payload), {
            "kind": "web",
            "tool": "search_web",
            **payload,
        }

    return search_web


def build_agent_tools(
    sensor_client: SensorMCPClient,
    retriever: Any,
    web_search: Any | None = None,
) -> tuple[list[BaseTool], BaseTool | None]:
    base_tools = [*build_mcp_tools(sensor_client), build_knowledge_tool(retriever)]
    web_tool = build_web_tool(web_search) if web_search is not None else None
    return base_tools, web_tool
