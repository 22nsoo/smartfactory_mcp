from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_tavily import TavilySearch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOOGLE_MODEL = "gemini-2.5-flash"


def load_project_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def build_google_llm() -> Any | None:
    load_project_environment()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    return ChatGoogleGenerativeAI(
        model=os.getenv("GOOGLE_MODEL", DEFAULT_GOOGLE_MODEL),
        api_key=api_key,
        temperature=0,
        max_tokens=int(os.getenv("GOOGLE_MAX_TOKENS", "1500")),
        thinking_budget=int(os.getenv("GOOGLE_THINKING_BUDGET", "0")),
        request_timeout=float(os.getenv("GOOGLE_REQUEST_TIMEOUT", "30")),
        retries=2,
    )


def build_tavily_search() -> Any | None:
    load_project_environment()
    if not os.getenv("TAVILY_API_KEY"):
        return None
    return TavilySearch(
        max_results=int(os.getenv("TAVILY_MAX_RESULTS", "3")),
        search_depth="basic",
        include_answer=False,
        include_raw_content=False,
        topic="general",
    )
