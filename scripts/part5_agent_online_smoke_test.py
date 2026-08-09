from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag.agent_workflow import build_tool_calling_agent


def main() -> None:
    agent = build_tool_calling_agent()
    if agent.llm is None:
        raise RuntimeError("GOOGLE_API_KEY is required for the online Agent test")
    result = agent.ask(
        "웹 검색으로 센서 92의 진동 이상 점검 자료와 저장된 상태를 함께 분석해줘"
    )
    if result["agent_mode"] != "tool_calling":
        raise RuntimeError(result)
    if result["generation_mode"] != "google_gemini_tool_agent":
        raise RuntimeError(f"Gemini Agent failed: {result['generation_error']}")
    if not result["tool_trace"]:
        raise RuntimeError("The Agent did not call any Tool")
    if not result["web_search_used"] or result["web_result_count"] < 1:
        raise RuntimeError(f"Tavily search failed: {result['web_search_error']}")
    if not any(item.get("type") == "web" for item in result["citations"]):
        raise RuntimeError("Web citations are missing")
    print("Part 5 online Tool-Calling Agent test passed")
    print(" → ".join(item["tool"] for item in result["tool_trace"]))
    print(result["answer"][:300].replace("\n", " "))


if __name__ == "__main__":
    main()
