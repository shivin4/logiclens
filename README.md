# LogicLens

**Version 1.1.0** — Local-first desktop app to explore a codebase as a **dependency graph**, run **semantic search**, see **git** context, and use **AI** (Groq + CrewAI) for explanations and “what-if” blast-radius style reports. No separate database server: the graph lives in **SQLite**, embeddings in **ChromaDB**, all under your machine’s app data folder.

---

## Highlights

- **Desktop-first:** `desktop_main.py` (or the packaged `.exe`) opens a native window (pywebview + Waitress).
- **First-run onboarding:** `/onboarding` — API keys, optional telemetry flag, folder picker, then analyze (opens automatically when Groq is not configured).
- **Incremental analyze:** Re-running **Analyze** on the same project only re-processes files whose **mtime + size** changed (plus adds/removes). Switching to another repo forces a **full** graph rebuild (single shared `logiclens_graph.db`).
- **Per-project Chroma:** Each analyzed folder gets its own collection (`ll_proj_<hash>`); vectors for other projects stay on disk.
- **Updates:** Settings → **Check for updates** calls GitHub Releases (`shivin4/logiclens` by default; overridable via env).
- **Optional telemetry:** Opt-in via UI; requires `SENTRY_DSN` in `.env` and `sentry-sdk`. **Nothing is sent** without a DSN.
- **Accessibility:** Visible **focus rings** on interactive controls; Settings dialog has **ARIA** labels, **Escape** closes, focus moves into the dialog when opened.

---

## Tech stack

| Layer | Choice |
|--------|--------|
| UI | Flask-served templates, Tailwind, vis-network |
| Server | Waitress (desktop), optional Flask dev server |
| Graph | SQLite (`logiclens_graph.db`) |
| Vectors | ChromaDB (persistent directory) |
| Parse | Tree-sitter |
| AI | Groq (explanations / LLM), CrewAI (what-if) |

---

## Supported languages

`.py` · `.js` / `.jsx` · `.ts` / `.tsx` · `.java` · `.go` · `.cpp` / `.cc` / `.h` / `.hpp`

---

## Quick start (development)

**Requirements:** Python 3.10+, Groq API key for AI features.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python desktop_main.py
```

If `GROQ_API_KEY` is not set in `%LOCALAPPDATA%\LogicLens\.env` (packaged) or your project `.env` (dev), the app opens **Onboarding** first.

**Browser-only debugging** (not the normal workflow):

```powershell
set LOGICLENS_DEBUG=1
python app.py
```

Then open `http://127.0.0.1:5000`.

---

## First run & API keys

1. **Onboarding** (`/onboarding`): Groq (required), Gemini (optional), optional crash-reporting checkbox, then pick a folder and **Analyze & open app**.
2. Or use **Settings → Open setup page** (`/setup`) anytime.

Keys are written to **`%LOCALAPPDATA%\LogicLens\.env`** when packaged, or your configured `LOGICLENS_DATA_DIR`.

See **`.env.example`** for all options (`LOGICLENS_FULL_ANALYZE`, telemetry, Sentry, etc.).

---

## How analyze works (simple)

| Situation | What happens |
|-----------|----------------|
| **First time** on a folder | Full rebuild: clear SQLite graph, recreate that project’s Chroma collection, walk all eligible files, build graph + vectors. |
| **Same folder again** | Incremental: only files with changed **mtime/size** (or new/deleted paths) are updated; rest skipped. |
| **Different folder** after another project | Full rebuild for the new folder (SQLite only holds one project at a time). |

Fingerprint manifest: `analysis_manifest_<collection>.json`. Force full: `LOGICLENS_FULL_ANALYZE=1`.

---

## Data on disk

All paths are under the **app data directory** unless you set `LOGICLENS_DATA_DIR`:

| Location | Role |
|----------|------|
| **App data directory** | Single “home” for local state. **Packaged:** `%LOCALAPPDATA%\LogicLens\`. **Dev:** usually the repo root unless `LOGICLENS_DATA_DIR` overrides. |
| **`logiclens_graph.db`** (SQLite) | **One file**, **one logical graph at a time**: whatever project you last analyzed successfully. It is **not** a multi-repo database; it is cleared or patched to match **one** workspace root. |
| **`chroma_data/`** (Chroma) | **Many collections** on disk. Each project folder gets its own collection name (`ll_proj_<hash of normalized path>`). Older projects’ vectors **remain** when you open another repo. |
| **`analysis_manifest_<collection>.json`** | Per-project **file fingerprint** ledger (`mtime_ns` + `size` per normalized path). Drives **incremental** vs **full** work for that root. |
| **`last_graph_project_root.txt`** | One line: normalized path of the project the **SQLite** file currently describes. If you analyze a **different** root, the code forces a **full** graph rebuild so incremental never runs on top of another project’s nodes. |
| **`.env`** in app data | API keys and optional overrides (from `/setup`, onboarding, or manual edit). |

**Why both SQLite and Chroma?** SQLite stores **structure** (nodes/edges, calls, API bridges). Chroma stores **embeddings** for **semantic search** and tools that need raw snippets. They are updated together for changed files.

---

## Updates

- **UI:** Settings → **Check for updates** — compares `logiclens/version.py` (or `LOGICLENS_APP_VERSION`) to `https://api.github.com/repos/<owner>/<repo>/releases/latest`.
- **Override repo:** `LOGICLENS_GITHUB_OWNER`, `LOGICLENS_GITHUB_REPO` in `.env`.

---

## Telemetry (privacy-first)

- **Default:** off. No analytics SDK runs unless `LOGICLENS_TELEMETRY=1` **and** `SENTRY_DSN` is set.
- **Behavior:** `sentry-sdk` + Flask integration; `send_default_pii=False`, `traces_sample_rate=0`.
- Install: included in `requirements.txt`; harmless if unused.

---

## Accessibility

- **Focus:** `:focus-visible` outline on buttons, links, inputs.
- **Settings modal:** `role="dialog"`, `aria-labelledby`, `aria-describedby`, labelled close button, **Escape** to dismiss, initial focus on the dialog panel.

---

## Packaging & trust (Windows)

| Doc | Purpose |
|-----|---------|
| **[packaging/BUILD_INSTALLER.md](packaging/BUILD_INSTALLER.md)** | PyInstaller → Inno Setup pipeline |
| **[packaging/SIGNING.md](packaging/SIGNING.md)** | Code-sign `LogicLens.exe` and the installer for fewer SmartScreen warnings |

**Version bump checklist:** sync `logiclens/version.py` (`_DEFAULT_VERSION`), `packaging/installer.iss` (`MyAppVersion`), `docs/index.html` (`INSTALLER_URL` asset name), and your GitHub Release asset filename.

```powershell
pyinstaller packaging\logiclens.spec
# Optional: sign dist\LogicLens\LogicLens.exe (see SIGNING.md)
# Compile packaging\installer.iss → dist_installer\LogicLens-Setup-1.1.0.exe
```

---

## Landing page

Static site: **[docs/index.html](docs/index.html)** — set `INSTALLER_URL` / `SOURCE_URL`, host on GitHub Pages / Netlify / Vercel — **[docs/DEPLOY.md](docs/DEPLOY.md)** (Pages: branch `main`, folder **`/docs`**).

---

## Project structure

| Path | Role |
|------|------|
| `app.py` | Flask app, REST API, template routes |
| `desktop_main.py` | Waitress + pywebview entry |
| `extractor.py` | Tree-sitter extract, incremental analyze, Chroma upserts |
| `whatif_engine.py` | Safe What-If entry (SSE); delegates to `whatif_crew.py` |
| `whatif_crew.py` | CrewAI implementation (optional deps) |
| `logiclens/config.py` | Paths, env, `normalize_project_file_path`, Chroma collection naming |
| `logiclens/sqlite_graph.py` | Graph CRUD, `delete_file_subgraph` |
| `logiclens/version.py` | `__version__`, default GitHub repo for updates |
| `logiclens/updates.py` | GitHub Releases latest check |
| `logiclens/telemetry.py` | Optional Sentry init |
| `templates/index.html` | Main UI |
| `templates/onboarding.html` | First-run wizard |
| `templates/setup.html` | API key form |
| `packaging/logiclens.spec` | PyInstaller |
| `packaging/installer.iss` | Inno Setup |

---

## API reference (selected)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main UI |
| GET | `/onboarding` | First-run wizard |
| GET | `/setup` | API key setup page |
| POST | `/setup` | Save keys + telemetry flags (JSON) |
| GET | `/api/bootstrap` | Data paths, `groq_configured`, `app_version`, etc. |
| GET | `/api/updates/check` | GitHub latest release metadata |
| POST | `/api/analyze` | Run `analyze_project` |
| POST | `/api/pick_folder` | Native folder dialog (localhost only) |
| GET | `/api/graph` | Full graph |
| GET | `/api/search?q=` | Semantic search (current project) |
| POST | `/api/whatif` | SSE what-if report |

---

## Limitations

- Large **vendor** trees and minified JS are skipped or capped (`LOGICLENS_MAX_JS_BYTES`).
- **`CALLS`** edges are strongest for callees **defined in the same file**; cross-file resolution is partial.
- **Incremental** change detection uses **mtime + size**, not a full-file hash (rare false “unchanged”).
- Duplicate **function names** across files can confuse naive name-based lookups in some AI tools.

---

## Security

- Never commit `.env`. Keys stay local.
- Pattern-based “vulnerability” hints are **heuristic**, not a full SAST.

---

## License

No license file is bundled in this repository; add one if you distribute forks.
