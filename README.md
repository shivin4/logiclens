# LogicLens

**Version 1.2.0** — a Windows desktop app that turns a project folder into an interactive map of files, functions and calls. You can search the code by meaning, trace who calls what, look at git history and check what a change could break.

Everything runs on your own PC. The graph is stored in SQLite and the search index in ChromaDB, so there is no database server to set up.

**Download:** grab `LogicLens-Setup-1.2.0.exe` from the [Releases page](https://github.com/shivin4/logiclens/releases/latest), or visit the [website](https://shivin4.github.io/logiclens/).

---

## What it does

- **Three graph views**
  - **Files**: one box per file, with lines for imports and calls between files. Good first look.
  - **Functions**: every function and class, grouped into one block per file (the Clusters layout).
  - **Single file**: pick a file, or double-click any node, to see just that file plus its callers and callees from other files.
- **Filters**: Classes, API routes, cross-file links, files and imports, unconnected items.
- **Legend**: a button on the graph explains every node shape, colour and line style.
- **Impact analysis**: search for a function, run the analysis and get an AI report with the affected files, callers and a risk rating. Reports can be copied or saved as `.md` / `.html`, and recent ones are kept.
- **Semantic search**: describe what you are looking for in plain words.
- **Git panel**: recent commits and files that change a lot.
- **GitHub repos**: analyze a public or private repo from its URL without cloning.
- **Explorer**: the sidebar file tree highlights files that are in the graph. Click one to open its graph.
- **Panels**: the right-hand panels can be dragged wider or narrower, or hidden.
- **Light and dark theme**: remembered for every window. Fonts and icons are bundled so the app works offline.

## Supported languages

Python, JavaScript, TypeScript (JSX/TSX), Vue, Svelte, Java, Kotlin, Scala, Go, Rust, C and C++, C#, Ruby, PHP, Swift, Dart, R, Julia and Jupyter notebooks.

---

## Run from source

You need Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python desktop_main.py
```

If no AI key is set, the app opens the onboarding page first. You can also add keys later in **Settings** or on `/setup`. Copy `.env.example` to `.env` to see all the options.

To debug in a browser instead of the desktop window:

```powershell
set LOGICLENS_DEBUG=1
python app.py
```

Then open `http://127.0.0.1:5000`.

## AI keys

The graph, views and search work without any key. Explanations and impact reports need one of:

- a Groq key (`GROQ_API_KEY`)
- a Gemini key (`GEMINI_API_KEY`)
- a local Ollama model

Keys are saved in `%LOCALAPPDATA%\LogicLens\.env` for the installed app, or in the project `.env` when running from source. Never commit `.env`.

---

## How analysis works

| Situation | What happens |
|-----------|--------------|
| First time on a folder | Full build: new graph and a new search collection for that project. |
| Same folder again | Only files whose modified time or size changed are re-read. |
| A different folder | Full rebuild, because the SQLite file holds one project at a time. |

After the files are parsed, `logiclens/crossfile.py` links them together:

- imports become `File → File` edges (Python, JS/TS, Java/Kotlin/Scala, Go, C/C++, Ruby, Rust, C#, PHP)
- calls between files are matched using the names a file actually imports. If a name could point to several files and the imports do not settle it, no link is drawn, which is better than a wrong one
- frontend `fetch` / `axios` calls are joined to backend routes

Set `LOGICLENS_FULL_ANALYZE=1` to force a full rebuild.

## Where data is stored

| What | Where |
|------|-------|
| App data folder | `%LOCALAPPDATA%\LogicLens\` when installed, the repo folder (or `LOGICLENS_DATA_DIR`) when developing |
| `logiclens_graph.db` | SQLite graph of the last analyzed project |
| `chroma_data/` | Search vectors, one collection per project |
| `analysis_manifest_*.json` | File fingerprints used for incremental analysis |
| `user_prefs.json` | Theme and tour state |
| `.env` | API keys and settings |

---

## Build the installer

```powershell
pip install pyinstaller
pyinstaller packaging\logiclens.spec --noconfirm
# then compile packaging\installer.iss with Inno Setup
```

The installer ends up in `dist_installer\`. More detail is in [packaging/BUILD_INSTALLER.md](packaging/BUILD_INSTALLER.md), and signing notes are in [packaging/SIGNING.md](packaging/SIGNING.md).

**Releasing a new version**

1. Update the version in `logiclens/version.py` and `packaging/installer.iss`.
2. Build the exe and the installer.
3. Create a GitHub Release with the tag `v<version>` and upload `LogicLens-Setup-<version>.exe`.

The website in `docs/` reads the latest release from GitHub, so its download button updates by itself. To host it, go to **Settings → Pages** and pick branch `main`, folder `/docs`.

---

## Project layout

| Path | What it is |
|------|------------|
| `app.py` | Flask app and REST API |
| `desktop_main.py` | Desktop window (pywebview + Waitress) |
| `extractor.py` | Tree-sitter parsing, incremental analysis |
| `whatif_engine.py`, `whatif_crew.py` | Impact analysis (CrewAI) |
| `logiclens/` | config, SQLite graph, cross-file linking, GitHub, updates |
| `templates/` | main UI, onboarding and setup pages |
| `static/` | bundled JS libraries and fonts |
| `docs/` | website |
| `packaging/` | PyInstaller spec and Inno Setup script |
| `samples/` | small files to try the app on |

## Limits

- Big vendor folders and minified JS are skipped or capped.
- Linking is done by name and imports, so calls built at runtime (for example `getattr`) are not found.
- Change detection uses modified time and size, not a content hash.
- The security hints are simple pattern checks, not a real security scanner.
- The installer is not code signed yet, so Windows SmartScreen may show a warning. Choose **More info → Run anyway**.

## License

No license file is included yet. Add one if you share forks.
