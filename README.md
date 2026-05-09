# LogicLens Code Dependency Analyzer

LogicLens is a local-first code intelligence tool that scans a repository, builds a dependency graph in Neo4j, indexes source blocks in ChromaDB, and serves an interactive Flask UI for graph exploration, semantic search, git hotspot analysis, and streamed "what-if" blast-radius reports.

## What It Does Today

- Parses supported source files with Tree-sitter.
- Extracts file/class/function entities and in-file call relationships.
- Detects simple hardcoded-secret and SQL-injection risk patterns.
- Builds a Neo4j graph with structural and API-bridge relationships.
- Indexes class/function source into ChromaDB for semantic retrieval.
- Exposes Flask APIs used by a single-page frontend for:
  - graph visualization,
  - source and dependency drill-down,
  - short AI explanations (Groq),
  - semantic code search (ChromaDB),
  - git commit/churn insights,
  - streamed CrewAI what-if analysis.

## Tech Stack

- Backend: Python, Flask
- Parsing: Tree-sitter (`tree-sitter-*` language modules)
- Graph database: Neo4j
- Vector store: ChromaDB (persistent local collection)
- AI: Groq API, CrewAI (Gemini package is installed but not used by the current explain endpoint)
- Frontend: server-rendered HTML template + JavaScript + Tailwind + vis-network

## Supported Languages

The extractor currently scans these extensions:

- `.py`
- `.js`, `.jsx`
- `.ts`, `.tsx`
- `.java`
- `.go`
- `.cpp`, `.cc`, `.h`, `.hpp`

## Architecture Overview

### 1) Analysis Pipeline

1. User submits a target folder path via `POST /api/analyze`.
2. `extractor.analyze_project()`:
   - clears existing Neo4j graph data,
   - resets Chroma collection `codebase_nodes`,
   - walks supported files (skipping common build/env folders),
   - parses classes/functions/calls/API-route patterns,
   - writes nodes/edges to Neo4j,
   - upserts source code blocks + metadata into ChromaDB.
3. UI calls APIs to render graph and details.

### 2) Data Stores

- Neo4j nodes: `File`, `Class`, `Function`, `APIRoute`
- Neo4j relationships:
  - `CONTAINS`
  - `CALLS`
  - `EXPOSES_API`
  - `CALLS_API`
- ChromaDB:
  - persistent path defaults to `./chroma_data`
  - collection name defaults to `codebase_nodes`

### 3) What-If Engine

`whatif_engine.py` builds a 3-agent CrewAI workflow that:

- finds direct callers from Neo4j,
- attempts to fetch caller source from ChromaDB,
- generates a markdown blast-radius report,
- streams updates/results as Server-Sent Events.

## Project Structure

- `app.py` - Flask app, API routes, UI serving
- `extractor.py` - Tree-sitter extraction + Neo4j/Chroma population
- `whatif_engine.py` - CrewAI what-if orchestration and SSE stream output
- `templates/index.html` - frontend UI
- `requirements.txt` - Python dependencies
- `list_models.py` - helper script for Gemini model listing
- `test_chroma.py` - manual Chroma query script

## Requirements

- Python 3.10+ recommended
- Running Neo4j instance
- Network access for Groq API (for AI explanation / what-if reports)

## Setup

1. Clone and enter the repository.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create `.env` in the project root:

```env
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=optional_currently_not_required_for_core_flow

NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password

# Optional overrides used by whatif_engine.py
CHROMA_PERSIST_PATH=./chroma_data
CHROMA_COLLECTION_NAME=codebase_nodes
```

## Run

Start the web app:

```bash
python app.py
```

Open:

- UI: `http://127.0.0.1:5000`

Optional direct CLI runs:

```bash
# Full extraction/indexing for a target path
python extractor.py "C:/path/to/repo"

# Run what-if engine directly for one function
python whatif_engine.py --function function_name
```

## API Reference

### Core

- `GET /` - UI page
- `POST /api/analyze` - Analyze a repository path
- `GET /api/graph` - Fetch all graph nodes/edges
- `GET /api/explorer?path=...` - Directory tree explorer
- `GET /api/trace?node_id=...` - Incoming/outgoing dependencies
- `GET /api/source?file=...&name=...&type=...` - Extract source block
- `GET /api/explain?file=...&name=...&type=...` - Groq short explanation + confidence

### Insights & AI

- `GET /api/functions` - Function name list from graph
- `POST /api/whatif` - Streamed what-if blast-radius report (SSE)
- `GET /api/git/summary` - Recent commits + top churn files
- `GET /api/search?q=...` - Semantic search in ChromaDB
- `GET /api/check_env` - Indicates whether `GROQ_API_KEY` is present

## Typical Workflow

1. Start the server.
2. Analyze a local repo path.
3. Explore the generated dependency graph.
4. Click a node to inspect source, trace dependencies, and request an AI explanation.
5. Run semantic search with natural language queries.
6. Use git summary to identify hotspots.
7. Trigger what-if analysis for a target function.

## Current Limitations

- Re-analysis is destructive: it clears existing Neo4j and Chroma data before indexing.
- `CALLS` edges are currently linked only when callee names are defined in the same file (cross-file resolution is limited).
- The what-if Chroma lookup may miss source blocks because extractor IDs include a file/type prefix while retrieval uses plain function names.
- `app.py` currently prints `GEMINI_API_KEY` at startup; avoid running with sensitive secrets in shared logs.
- No automated unit/integration test suite is included yet.
- No dedicated lint/format CI pipeline is configured.

## Troubleshooting

- `Invalid directory path` on analyze:
  - ensure the submitted path exists and is accessible.
- Neo4j connection/auth issues:
  - verify `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.
- Search endpoint says collection not found:
  - run analysis first to create/populate `codebase_nodes`.
- What-if errors about missing dependencies:
  - install all requirements, and ensure CrewAI-related dependencies resolve correctly in your environment.
- AI explanation unavailable:
  - set `GROQ_API_KEY` in `.env` and restart the server.

## Security Notes

- Keep `.env` local and never commit API keys.
- The extractor does best-effort pattern scanning for risky literals, but this is not a complete security scanner.

## License

No license file is currently included in this repository.
