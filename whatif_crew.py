"""
LogicLens — CrewAI implementation for What-If / impact analysis.

Imported only when the CrewAI stack is available. The public entrypoint for the
app is ``whatif_engine.run_whatif_engine`` (safe when deps are missing).

Run from CLI:
    python whatif_crew.py
    python whatif_crew.py --function my_function_name
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def _bootstrap_tiktoken_encodings() -> None:
    """PyInstaller / frozen builds often break ``pkgutil.iter_modules(tiktoken_ext)``, so
    LiteLLM sees ``Plugins found: []`` and raises ``Unknown encoding cl100k_base``.
    Pre-register constructors from ``tiktoken_ext.openai_public`` before CrewAI imports LiteLLM.
    """
    try:
        import tiktoken.registry as _tikreg
        import tiktoken_ext.openai_public as _tikplug
    except ImportError:
        return
    with _tikreg._lock:
        if _tikreg.ENCODING_CONSTRUCTORS is None:
            _tikreg.ENCODING_CONSTRUCTORS = {}
        _tikreg.ENCODING_CONSTRUCTORS.update(_tikplug.ENCODING_CONSTRUCTORS)


_bootstrap_tiktoken_encodings()

# ── Third-party imports ────────────────────────────────────────────────────────
try:
    from crewai import Agent, Crew, Process, Task, LLM
    from crewai.tools import BaseTool
    import chromadb
except ImportError as e:
    raise ImportError(
        f"{e}. Install with: pip install crewai langchain-openai litellm groq chromadb python-dotenv"
    ) from e

# ──────────────────────────────────────────────────────────────────────────────
# 0.  Configuration (read from .env)
# ──────────────────────────────────────────────────────────────────────────────

from logiclens.config import (
    chroma_collection_for_project,
    chroma_collection_name,
    chroma_dir,
    graph_db_path,
    load_app_env,
)
from logiclens.sqlite_graph import SqliteGraphStore

load_app_env()

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    print("[WARNING] GROQ_API_KEY is not set. What-If engine will report a system error if run.")

# ──────────────────────────────────────────────────────────────────────────────
# 1.  Custom CrewAI Tools  (BaseTool subclasses — required by CrewAI's validator)
# ──────────────────────────────────────────────────────────────────────────────


class GraphBlastRadiusTool(BaseTool):
    """
    Queries the embedded SQLite call-graph to find every function that *directly*
    calls the given target function (i.e. its immediate blast radius).

    Input:  target_function — the exact name of the function to inspect.
    Output: Newline-separated list of affected caller function names,
            or an explanatory error string on failure.
    """

    name: str = "graph_blast_radius_tool"
    description: str = (
        "Use this tool to find all functions that directly call a given target "
        "function in the codebase dependency graph (local SQLite store). "
        "Input: the exact name of the target function (a plain string). "
        "Output: a list of caller function names."
    )

    def _run(self, target_function: str) -> str:  # type: ignore[override]
        target_function = target_function.strip().strip('"').strip("'")

        try:
            store = SqliteGraphStore(graph_db_path())
            affected = store.callers_of_function_name(target_function)
        except Exception as exc:  # noqa: BLE001
            return f"[Graph ERROR] Unexpected error: {exc}"

        if not affected:
            return (
                f"No callers found for '{target_function}'. "
                "Either the function is a root node or it does not exist in the graph."
            )

        return (
            f"Functions that call '{target_function}' "
            f"({len(affected)} found):\n"
            + "\n".join(f"  - {name}" for name in affected)
        )


class ChromaDBSourceCodeTool(BaseTool):
    """
    Fetches the raw source code of one or more functions from ChromaDB.

    Input:  function_names_csv — comma-separated function names
            (e.g. "calculate_tax,apply_discount,process_order").
    Output: Formatted source code blocks for each function,
            or an explanatory error string on failure.
    """

    name: str = "chromadb_source_code_tool"
    description: str = (
        "Use this tool to retrieve the raw source code of one or more functions "
        "from the ChromaDB vector store. "
        "Input: a comma-separated list of function names "
        "(e.g. 'calculate_tax,apply_discount'). "
        "Output: the source code and metadata for each function."
    )

    def _run(self, function_names_csv: str) -> str:  # type: ignore[override]
        # Parse the CSV input — agents sometimes wrap it in quotes or add spaces
        raw = function_names_csv.strip().strip('"').strip("'")
        names: list[str] = [n.strip() for n in raw.split(",") if n.strip()]

        if not names:
            return "[ChromaDB ERROR] No function names provided."

        try:
            client = chromadb.PersistentClient(path=str(chroma_dir()))
            active = os.environ.get("LOGICLENS_ACTIVE_PROJECT") or ""
            coll_name = (
                chroma_collection_for_project(active)
                if active.strip()
                else chroma_collection_name()
            )
            collection = client.get_collection(name=coll_name)
        except Exception as exc:  # noqa: BLE001
            return f"[ChromaDB ERROR] Could not connect to collection: {exc}"

        output_parts: list[str] = []
        not_found: list[str] = []

        for name in names:
            try:
                result = collection.get(
                    where={"name": name},
                    include=["documents", "metadatas"],
                    limit=50,
                )
                docs = result.get("documents") or []
                metas = result.get("metadatas") or []

                if not docs or all(d is None for d in docs):
                    not_found.append(name)
                    continue

                for idx, source_code in enumerate(docs):
                    if source_code is None:
                        continue
                    meta: dict = metas[idx] if idx < len(metas) and metas[idx] else {}
                    header = (
                        f"### Function: `{name}`\n"
                        f"- **File:** {meta.get('filepath', 'N/A')}\n"
                        f"- **Lines:** {meta.get('start_line', '?')}–{meta.get('end_line', '?')}\n"
                        f"- **Author:** {meta.get('author', 'Unknown')}\n"
                    )
                    output_parts.append(f"{header}\n```python\n{source_code}\n```")

            except Exception as exc:  # noqa: BLE001
                output_parts.append(f"### Function: `{name}`\n[ERROR] {exc}")

        if not_found:
            output_parts.append(
                "\n**⚠ Not found in ChromaDB:** "
                + ", ".join(f"`{n}`" for n in not_found)
            )

        return "\n\n---\n\n".join(output_parts) if output_parts else (
            "[ChromaDB] No source code could be retrieved for the provided names."
        )


# Instantiate tools once so agents share the same objects
graph_blast_radius_tool = GraphBlastRadiusTool()
chromadb_source_code_tool = ChromaDBSourceCodeTool()


# ──────────────────────────────────────────────────────────────────────────────
# 2.  LLM
# ──────────────────────────────────────────────────────────────────────────────

def build_llm() -> LLM:
    """Initialise the Groq LLM used by all agents."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set in the environment.")
    return LLM(
        model="groq/llama-3.3-70b-versatile",
        temperature=0.2,
        api_key=api_key,
    )


def _format_sse(role: str, content: str, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"role": role, "content": content}
    if extra:
        payload.update(extra)
    return f"data: {json.dumps(payload)}\n\n"


def _is_missing_litellm_package(exc: BaseException, tb_text: str) -> bool:
    """True only when LiteLLM truly failed to import — not any random ImportError."""
    blob = f"{type(exc).__name__}: {exc}\n{tb_text}".lower()
    if "no module named 'litellm'" in blob or 'no module named "litellm"' in blob:
        return True
    if "modulenotfounderror" in blob and "litellm" in blob:
        return True
    if "importerror" in blob and "litellm" in blob and "cannot import" in blob:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Agent Definitions
# ──────────────────────────────────────────────────────────────────────────────

def build_agents(llm: LLM) -> tuple[Agent, Agent, Agent]:
    """Create and return the three specialist agents."""

    # --- Agent 1: The Investigator -------------------------------------------
    investigator = Agent(
        role="Senior Codebase Graph Analyst",
        goal=(
            "Use the graph blast-radius tool to discover the precise structural "
            "call-graph dependencies of a given function. Return a definitive, "
            "complete list of every function that directly calls the target."
        ),
        backstory=(
            "You are a seasoned software architect who has spent years mapping "
            "complex call-graphs and identifying hidden coupling in large codebases. "
            "You trust data from the graph database above all else and always verify "
            "your findings before passing them downstream."
        ),
        tools=[graph_blast_radius_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    # --- Agent 2: The Semantic Architect -------------------------------------
    semantic_architect = Agent(
        role="Source Code Logic Reviewer",
        goal=(
            "Fetch the raw source code of the affected functions identified by "
            "the Investigator from ChromaDB, then deeply analyse *how* each caller "
            "interacts with the target function — arguments passed, return values "
            "consumed, shared side-effects, and critical execution paths."
        ),
        backstory=(
            "You are a principal engineer who specialises in reading code at depth. "
            "You identify subtle logic bugs, incorrect assumptions about a function's "
            "contract, and potential runtime failures before they reach production."
        ),
        tools=[chromadb_source_code_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    # --- Agent 3: The Explainer ----------------------------------------------
    explainer = Agent(
        role="Principal Software Architect",
        goal=(
            "Synthesise the structural dependency data from the Investigator and "
            "the semantic code analysis from the Semantic Architect into a single, "
            "clear, actionable Markdown report that any developer can understand."
        ),
        backstory=(
            "You have led engineering teams at top-tier companies and excel at "
            "translating complex technical findings into crisp, prioritised reports. "
            "Your documentation enables teams to ship safer changes faster."
        ),
        tools=[],  # Explainer only synthesises — no database access needed
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    return investigator, semantic_architect, explainer


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Task Definitions
# ──────────────────────────────────────────────────────────────────────────────

def build_tasks(
    investigator: Agent,
    semantic_architect: Agent,
    explainer: Agent,
    target_function: str,
) -> tuple[Task, Task, Task]:
    """Create and return the three sequential tasks."""

    # --- Task 1: Structural Dependency Discovery ------------------------------
    task_graph = Task(
        description=(
            f"Use the `graph_blast_radius_tool` to find **every function** in the "
            f"codebase that directly calls `{target_function}`.\n\n"
            "Your output MUST be:\n"
            "1. A numbered list of all caller function names.\n"
            "2. A one-sentence summary of the total blast radius size.\n\n"
            "If the tool returns no callers, state that clearly and note it may be "
            "a root or entry-point function."
        ),
        expected_output=(
            "A numbered list of caller function names and a one-sentence "
            "blast-radius summary."
        ),
        agent=investigator,
    )

    # --- Task 2: Semantic Source Code Analysis --------------------------------
    task_semantic = Task(
        description=(
            "You have been given a list of functions that call "
            f"`{target_function}` (output of Task 1).\n\n"
            "Steps:\n"
            "1. Call `chromadb_source_code_tool` with those function names as a "
            "   comma-separated string.\n"
            "2. For each function whose source code is returned, analyse:\n"
            "   a. How it calls the target (arguments, frequency, branching).\n"
            "   b. What it does with the return value of the target.\n"
            "   c. Whether a signature change, renamed parameter, or altered return "
            "      type in the target would BREAK this caller.\n"
            "   d. Any shared mutable state or side-effects.\n\n"
            "Return a structured per-function analysis."
        ),
        expected_output=(
            "A structured per-function analysis covering calling pattern, "
            "return-value usage, breakage risk, and side-effects."
        ),
        agent=semantic_architect,
        context=[task_graph],
    )

    # --- Task 3: Blast Radius Report ------------------------------------------
    task_report = Task(
        description=(
            f"Synthesise the outputs of Task 1 and Task 2 into a comprehensive "
            f"**Blast Radius Report** for the function `{target_function}`.\n\n"
            "The report MUST be valid Markdown and contain these exact sections:\n\n"
            "## Target Function\n"
            "Name, and a one-line description of its likely purpose "
            "(infer from the callers if source is unavailable).\n\n"
            "## Blast Radius Summary\n"
            "Total number of callers, risk level (Low / Medium / High / Critical), "
            "and a two-sentence executive summary.\n\n"
            "## Affected Files & Functions\n"
            "A table: | Function | File | Risk Level | Reason |\n\n"
            "## Detailed Logic Risk Analysis\n"
            "For each affected function: what specifically would break and why.\n\n"
            "## Testing Recommendations\n"
            "A prioritised checklist of unit/integration tests that must be written "
            "or updated before changing the target function.\n\n"
            "## Refactoring Suggestions (Optional)\n"
            "Any architectural improvements that could reduce future blast radius.\n\n"
            "Ensure the report is professional, precise, and directly actionable.\n"
            "CRITICAL INSTRUCTION: DO NOT use any emojis whatsoever in the report. Keep the formatting completely professional, clean, and strictly structured."
        ),
        expected_output=(
            "A complete, properly formatted Markdown Blast Radius Report with all "
            "six required sections."
        ),
        agent=explainer,
        context=[task_graph, task_semantic],
        output_file="blast_radius_report.md",
    )

    return task_graph, task_semantic, task_report


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Crew Assembly & Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def run_whatif_crew(target_function: str):
    """
    Assemble the crew and stream What-If analysis events.

    Yields SSE-formatted strings that can be consumed by the frontend.
    """
    print("\n" + "=" * 70)
    print(f"  LogicLens — What-If Engine  |  Target: '{target_function}'")
    print("=" * 70 + "\n")

    try:
        yield _format_sse(
            "system",
            f"Starting What-If analysis for: {target_function}",
            {"type": "start", "function": target_function},
        )

        llm = build_llm()
        investigator, semantic_architect, explainer = build_agents(llm)
        task_graph, task_semantic, task_report = build_tasks(
            investigator, semantic_architect, explainer, target_function
        )

        print("Agents Initialized")
        yield _format_sse(
            "system",
            "Agents initialized successfully.",
            {"type": "log", "text": "Agents initialized successfully."},
        )

        crew = Crew(
            agents=[investigator, semantic_architect, explainer],
            tasks=[task_graph, task_semantic, task_report],
            process=Process.sequential,
            verbose=True,
        )

        print("Crew kickoff started")
        yield _format_sse(
            "system",
            "Running the agent crew (this can take a minute)…",
            {"type": "log", "text": "Running the agent crew (this can take a minute)…"},
        )

        try:
            result = crew.kickoff(inputs={"target_function": target_function})
            final_md = str(result)
            print("What-If analysis complete")
            yield _format_sse(
                "assistant",
                final_md,
                {"type": "done", "report": final_md},
            )
        except Exception as kickoff_exc:
            import traceback
            error_text = traceback.format_exc()
            print("Crew kickoff failed:", error_text)

            exc_str = str(kickoff_exc).lower() + error_text.lower()
            if _is_missing_litellm_package(kickoff_exc, error_text):
                yield _format_sse(
                    "system",
                    "LiteLLM is not installed or not bundled. "
                    "Dev: pip install litellm. Packaged app: rebuild with PyInstaller so litellm is included.",
                    {
                        "type": "error",
                        "text": "LiteLLM import failed. Dev: pip install litellm. Frozen .exe: rebuild the app.\n\n"
                        + error_text,
                    },
                )
            elif "authentication" in exc_str or "invalid_api_key" in exc_str or "invalid api key" in exc_str:
                yield _format_sse(
                    "system",
                    "Invalid Groq Key",
                    {"type": "error", "text": "Invalid Groq Key"}
                )
            elif "insufficient_quota" in exc_str or "rate_limit" in exc_str or "429" in exc_str:
                yield _format_sse(
                    "system",
                    "⚠️ API Rate Limit or Quota Exceeded. Please check your Groq limits.",
                    {"type": "error", "text": "⚠️ API Rate Limit or Quota Exceeded. Please check your Groq limits."}
                )
            else:
                yield _format_sse(
                    "system",
                    f"Crew kickoff failed: {kickoff_exc}",
                    {"type": "error", "text": str(kickoff_exc) + "\n" + error_text},
                )

    except Exception as exc:
        import traceback
        error_text = traceback.format_exc()
        print("System Error during What-If execution:\n", error_text)

        if _is_missing_litellm_package(exc, error_text):
            yield _format_sse(
                "system",
                "LiteLLM is not installed or not bundled. "
                "Dev: pip install litellm. Packaged app: rebuild with PyInstaller so litellm is included.",
                {
                    "type": "error",
                    "text": "LiteLLM import failed. Dev: pip install litellm. Frozen .exe: rebuild the app.\n\n"
                    + error_text,
                },
            )
        else:
            exc_str = str(exc).lower() + error_text.lower()
            if (
                "authentication" in exc_str
                or "invalid_api_key" in exc_str
                or "invalid api key" in exc_str
            ):
                yield _format_sse(
                    "system",
                    "Invalid Groq Key",
                    {"type": "error", "text": "Invalid Groq Key"},
                )
            else:
                yield _format_sse(
                    "system",
                    "System Error: A backend initialization failure occurred. Check server logs.",
                    {"type": "error", "text": error_text},
                )


# ──────────────────────────────────────────────────────────────────────────────
# 6.  __main__
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LogicLens Phase 4 — What-If Explanation Engine"
    )
    parser.add_argument(
        "--function",
        type=str,
        default="predict_risk",
        help="Name of the target function to analyse (default: 'predict_risk')",
    )
    args = parser.parse_args()

    for event in run_whatif_crew(target_function=args.function):
        print(event, end='')
