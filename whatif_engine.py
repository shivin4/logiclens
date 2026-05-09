"""
Public What-If entrypoint for LogicLens (Flask / desktop).

Safe to import even when the CrewAI stack is not installed: missing dependencies
yield an SSE error instead of terminating the server (``sys.exit`` must never run
from an HTTP handler — it would kill Waitress and the UI shows "Connection failed").
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Iterator


def _format_sse(role: str, content: str, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"role": role, "content": content}
    if extra:
        payload.update(extra)
    return f"data: {json.dumps(payload)}\n\n"


def run_whatif_engine(target_function: str) -> Iterator[str]:
    try:
        from whatif_crew import run_whatif_crew
    except ImportError as exc:
        msg = (
            "What-If dependencies are not available in this environment. "
            f"{exc}\n"
            "Install with: pip install crewai langchain-openai litellm groq chromadb"
        )
        yield _format_sse("system", msg, {"type": "error", "text": msg})
        return
    yield from run_whatif_crew(target_function)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LogicLens — What-If Explanation Engine (delegates to whatif_crew)"
    )
    parser.add_argument(
        "--function",
        type=str,
        default="predict_risk",
        help="Name of the target function to analyse (default: 'predict_risk')",
    )
    args = parser.parse_args()

    for event in run_whatif_engine(target_function=args.function):
        print(event, end="")
