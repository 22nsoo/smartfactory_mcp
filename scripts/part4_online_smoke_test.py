from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag.workflow import build_agent


def main() -> None:
    agent = build_agent()
    result = agent.ask("웹 검색으로 센서 92의 진동 이상 점검 자료와 저장된 상태를 함께 설명해줘")
    if result["route"] != "hybrid":
        raise RuntimeError(f"Expected hybrid route: {result['route']}")
    if result["generation_mode"] != "google_gemini":
        raise RuntimeError(f"Gemini generation failed: {result['generation_error']}")
    if not result["web_search_used"] or result["web_result_count"] < 1:
        raise RuntimeError(f"Tavily search failed: {result['web_search_error']}")
    if not any(item.get("type") == "web" for item in result["citations"]):
        raise RuntimeError("Web citations are missing")
    print("Part 4 online smoke test passed")
    print(
        f"route={result['route']} generation={result['generation_mode']} "
        f"web_results={result['web_result_count']}"
    )
    print(result["answer"][:300].replace("\n", " "))


if __name__ == "__main__":
    main()
