from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.client import SensorMCPClient
from rag.workflow import SmartFactoryAgent


class FakeRouter:
    def invoke(self, prompt: str):
        if "웹 검색" in prompt and "점검" in prompt:
            return {
                "route": "hybrid",
                "sensor_id": "92",
                "needs_web": True,
                "reason": "상태 조회와 점검 지식이 모두 필요합니다.",
            }
        return {
            "route": "sensor",
            "sensor_id": "92",
            "needs_web": False,
            "reason": "센서 상태 조회 질문입니다.",
        }


class FakeGemini:
    def with_structured_output(self, schema):
        return FakeRouter()

    def invoke(self, prompt: str) -> SimpleNamespace:
        if "2019년 공개 데이터" not in prompt or "Risk Score" not in prompt:
            raise RuntimeError("Safety instructions are missing from the prompt")
        return SimpleNamespace(
            content="모의 Gemini 답변",
            response_metadata={"finish_reason": "STOP"},
        )


class FakeTruncatedGemini:
    def with_structured_output(self, schema):
        return FakeRouter()

    def invoke(self, prompt: str) -> SimpleNamespace:
        return SimpleNamespace(
            content="중간에 잘린",
            response_metadata={"finish_reason": "MAX_TOKENS"},
        )


class FakeTavily:
    def invoke(self, payload: dict[str, str]) -> dict:
        if not payload.get("query"):
            raise RuntimeError("Search query is missing")
        return {
            "results": [
                {
                    "title": "모의 외부 자료",
                    "url": "https://example.com/maintenance",
                    "content": "외부 검색 흐름 검증용 데이터",
                }
            ]
        }


def main() -> None:
    sensor_client = SensorMCPClient()
    agent = SmartFactoryAgent(sensor_client, llm=FakeGemini(), web_search=FakeTavily())
    result = agent.ask("웹 검색으로 센서 92 상태와 점검 방법을 알려줘")
    if result["route"] != "hybrid":
        raise RuntimeError(result)
    if result["router_mode"] != "google_gemini_structured":
        raise RuntimeError(result)
    if result["generation_mode"] != "google_gemini":
        raise RuntimeError(result)
    if not result["web_search_used"] or result["web_result_count"] != 1:
        raise RuntimeError(result)
    if not any(item.get("type") == "web" for item in result["citations"]):
        raise RuntimeError(result)

    truncated_agent = SmartFactoryAgent(sensor_client, llm=FakeTruncatedGemini())
    truncated = truncated_agent.ask("센서 92 상태 알려줘")
    if truncated["generation_mode"] != "deterministic_fallback":
        raise RuntimeError(truncated)
    if truncated["generation_error"] != "MAX_TOKENS":
        raise RuntimeError(truncated)
    if "센서 92의 마지막 저장 상태" not in truncated["answer"]:
        raise RuntimeError("Truncated answer was not replaced by the complete fallback")
    print("Part 4 mocked integration test passed")


if __name__ == "__main__":
    main()
