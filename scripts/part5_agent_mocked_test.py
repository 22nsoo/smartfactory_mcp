from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, ToolMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag.agent_workflow import ToolCallingSmartFactoryAgent
from web_app.app import create_app


def tool_call(name: str, arguments: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}
        ],
    )


class FakeSensorClient:
    def __init__(self, fail_tool: str | None = None):
        self.fail_tool = fail_tool
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == self.fail_tool:
            raise RuntimeError("secret database detail must not be exposed")
        if name == "get_sensor_status":
            return {
                "model_run_id": "mock-run",
                "sensor_id": arguments["sensor_id"],
                "as_of": "2019-07-30 20:42:00",
                "is_historical_data": True,
                "status": "DEGRADING",
                "risk_score": 81.83,
                "unit": "mg",
                "sample_count": 60,
                "rms": 42.1,
                "peak_to_peak": 18.4,
            }
        if name == "get_sensor_history":
            return {
                "sensor_id": arguments["sensor_id"],
                "as_of": "2019-07-30 20:42:00",
                "returned_count": 2,
                "windows": [
                    {"window_start": "2019-07-30 20:42:00", "risk_score": 81.83},
                    {"window_start": "2019-07-30 20:41:00", "risk_score": 74.2},
                ],
            }
        if name == "get_factory_summary":
            return {
                "as_of": "2019-07-30 20:42:00",
                "monitored_sensor_count": 3,
                "normal_count": 1,
                "attention_count": 0,
                "degrading_count": 2,
                "warning_count": 0,
            }
        if name == "get_abnormal_sensors":
            return {
                "as_of": "2019-07-30 20:42:00",
                "sensors": [
                    {"sensor_id": "92", "status": "DEGRADING", "risk_score": 81.83}
                ],
            }
        if name == "list_monitored_sensors":
            return {"sensor_count": 3, "sensors": [{"sensor_id": "92"}]}
        if name == "get_model_summary":
            return {"model_run_id": "mock-run", "is_historical_data": True}
        if name == "get_anomaly_detail":
            return {"sensor_id": arguments["sensor_id"], **arguments}
        raise RuntimeError(f"unexpected tool: {name}")


class FakeRetriever:
    def invoke(self, query: str) -> list[Document]:
        return [
            Document(
                page_content="진동 상승 시 센서 체결 상태와 케이블을 먼저 확인한다.",
                metadata={
                    "source": "vibration_triage.md",
                    "title": "진동 상승 시 확인 순서",
                    "chunk": 2,
                },
            )
        ]


class FakeWebSearch:
    def __init__(self):
        self.call_count = 0

    def invoke(self, payload: dict[str, str]) -> dict[str, Any]:
        self.call_count += 1
        return {
            "results": [
                {
                    "title": "공식 점검 자료",
                    "url": "https://example.com/maintenance",
                    "content": "진동 설비 점검 자료",
                }
            ]
        }


class ScriptedLLM:
    def __init__(self, messages: list[AIMessage]):
        self.messages = list(messages)
        self.bound_tool_names: list[list[str]] = []
        self.observed_tool_messages: list[int] = []

    def bind_tools(self, tools):
        self.bound_tool_names.append([item.name for item in tools])
        return self

    def invoke(self, messages):
        self.observed_tool_messages.append(
            sum(isinstance(item, ToolMessage) for item in messages)
        )
        if not self.messages:
            raise RuntimeError("No scripted model response")
        return self.messages.pop(0)


class LoopingLLM:
    def __init__(self):
        self.index = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.index += 1
        return tool_call("get_sensor_status", {"sensor_id": "92"}, f"loop-{self.index}")


def build_agent(llm, sensor_client=None, web_search=None, max_steps=8):
    return ToolCallingSmartFactoryAgent(
        sensor_client or FakeSensorClient(),
        llm=llm,
        web_search=web_search,
        retriever=FakeRetriever(),
        max_steps=max_steps,
    )


def test_single_tool() -> None:
    llm = ScriptedLLM(
        [
            tool_call("get_sensor_status", {"sensor_id": "92"}, "status-1"),
            AIMessage(content="2019년 마지막 저장 시각 기준 센서 92는 DEGRADING입니다."),
        ]
    )
    result = build_agent(llm).ask("센서 92 상태 알려줘")
    assert [item["tool"] for item in result["tool_trace"]] == ["get_sensor_status"]
    assert result["generation_mode"] == "google_gemini_tool_agent"
    assert result["route"] == "agent"


def test_multi_step() -> None:
    llm = ScriptedLLM(
        [
            tool_call("get_sensor_status", {"sensor_id": "92"}, "multi-1"),
            tool_call(
                "get_sensor_history",
                {"sensor_id": "92", "hours": 24, "limit": 50},
                "multi-2",
            ),
            tool_call(
                "search_maintenance_knowledge",
                {"query": "진동 위험 점검"},
                "multi-3",
            ),
            AIMessage(
                content=(
                    "2019년 저장 데이터 기준 이상 후보입니다. "
                    "센서 체결 상태를 확인하세요 [출처: vibration_triage.md]. "
                    "현장 안전 절차와 제조사 매뉴얼을 우선해야 합니다."
                )
            ),
        ]
    )
    result = build_agent(llm).ask("센서 92가 왜 이상한지 분석해줘")
    assert [item["tool"] for item in result["tool_trace"]] == [
        "get_sensor_status",
        "get_sensor_history",
        "search_maintenance_knowledge",
    ]
    assert llm.observed_tool_messages == [0, 1, 2, 3]
    assert result["retrieved_document_count"] == 1
    assert result["citations"][0]["source"] == "vibration_triage.md"


def test_knowledge_only() -> None:
    llm = ScriptedLLM(
        [
            tool_call(
                "search_maintenance_knowledge",
                {"query": "진동 상승 점검"},
                "knowledge-1",
            ),
            AIMessage(content="체결 상태를 확인하세요 [출처: vibration_triage.md]."),
        ]
    )
    result = build_agent(llm).ask("진동 상승 시 무엇을 점검해야 해?")
    assert [item["tool"] for item in result["tool_trace"]] == [
        "search_maintenance_knowledge"
    ]
    assert result["sensor_data"] is None


def test_web_permission() -> None:
    blocked_search = FakeWebSearch()
    blocked_llm = ScriptedLLM([AIMessage(content="로컬 근거만 사용했습니다.")])
    build_agent(blocked_llm, web_search=blocked_search).ask("센서 92 상태 알려줘")
    assert "search_web" not in blocked_llm.bound_tool_names[0]
    assert blocked_search.call_count == 0

    forced_search = FakeWebSearch()
    forced_llm = ScriptedLLM(
        [
            tool_call("search_web", {"query": "허가 없는 검색"}, "web-blocked"),
            AIMessage(content="외부 검색은 실행하지 않았습니다."),
        ]
    )
    forced = build_agent(forced_llm, web_search=forced_search).ask("센서 92 상태 알려줘")
    assert forced_search.call_count == 0
    assert forced["web_search_error"] == "external_web_search_not_authorized"

    allowed_llm = ScriptedLLM(
        [
            tool_call("search_web", {"query": "진동 최신 점검 자료"}, "web-1"),
            AIMessage(content="외부 자료를 확인했습니다. https://example.com/maintenance"),
        ]
    )
    allowed_search = FakeWebSearch()
    result = build_agent(allowed_llm, web_search=allowed_search).ask(
        "웹 검색으로 진동 최신 점검 자료를 찾아줘"
    )
    assert "search_web" in allowed_llm.bound_tool_names[0]
    assert result["web_search_used"] is True
    assert result["web_result_count"] == 1
    assert allowed_search.call_count == 1


def test_tool_error() -> None:
    client = FakeSensorClient(fail_tool="get_sensor_history")
    llm = ScriptedLLM(
        [
            tool_call(
                "get_sensor_history",
                {"sensor_id": "92", "hours": 24, "limit": 50},
                "error-1",
            ),
            AIMessage(content="이력 조회에 실패해 원인을 확정할 수 없습니다."),
        ]
    )
    result = build_agent(llm, sensor_client=client).ask("센서 92 추세를 분석해줘")
    assert result["tool_trace"][0]["status"] == "error"
    assert "secret database detail" not in result["answer"]


def test_max_step() -> None:
    result = build_agent(LoopingLLM(), max_steps=2).ask("센서 92를 계속 조회해줘")
    assert result["generation_error"] == "agent_step_limit"
    assert result["agent_step_count"] == 2
    assert result["tool_trace"][-1]["status"] == "not_executed"
    assert "추가 조회 한도" in result["answer"]


def test_offline_and_flask() -> None:
    agent = build_agent(None)
    result = agent.ask("센서 92 상태와 점검 방법을 알려줘")
    assert result["generation_mode"] == "deterministic_offline_fallback"
    assert result["retrieved_document_count"] == 1

    app = create_app(repository=object(), rag_agent=agent)
    app.testing = True
    client = app.test_client()
    response = client.post("/api/ask", json={"question": "센서 92 상태 알려줘"})
    assert response.status_code == 200
    assert response.get_json()["route"] == "agent"
    invalid = client.post("/api/ask", json={"question": ""})
    assert invalid.status_code == 400


def main() -> None:
    test_single_tool()
    test_multi_step()
    test_knowledge_only()
    test_web_permission()
    test_tool_error()
    test_max_step()
    test_offline_and_flask()
    print("Part 5 mocked Tool-Calling Agent tests passed")


if __name__ == "__main__":
    main()
