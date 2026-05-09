# PyInstaller spec — run from repo root:
#   pip install pyinstaller
#   pyinstaller packaging/logiclens.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
root = Path(SPECPATH).resolve().parent

datas = [
    (str(root / "templates"), "templates"),
    (str(root / "static"), "static"),
    (str(root / "logo"), "logo"),
    (str(root / "logiclens"), "logiclens"),
]

# CrewAI reads ``crewai/translations/en.json`` via ``utilities/i18n.py`` at runtime.
# Hidden-import hooks only pull ``.py`` files, so the frozen build was missing this
# tree and failed with FileNotFoundError / "Prompt file 'None' not found."
try:
    import crewai as _crewai_bundle

    _crew_translations = Path(_crewai_bundle.__file__).resolve().parent / "translations"
    if _crew_translations.is_dir():
        datas.append((str(_crew_translations), "crewai/translations"))
except Exception:
    pass


def _safe_collect_all(package: str):
    try:
        return collect_all(package)
    except Exception:
        return [], [], []


def _safe_subs(package: str, skip_prefixes: tuple[str, ...]):
    try:
        return collect_submodules(
            package,
            on_error="ignore",
            filter=lambda name: not any(
                name.startswith(p) for p in skip_prefixes
            ),
        )
    except Exception:
        return []


# ChromaDB 1.x: native ``chromadb_rust_bindings`` + deep imports are often missed.
_rb_datas, _rb_binaries, _rb_hidden = _safe_collect_all("chromadb_rust_bindings")
_chroma_hidden = collect_submodules(
    "chromadb",
    on_error="ignore",
    filter=lambda name: not name.startswith(
        ("chromadb.test", "chromadb.server")
    ),
)

# Chroma default embeddings / deps: binaries and metadata are easy to miss.
_onnx_d, _onnx_b, _onnx_h = _safe_collect_all("onnxruntime")
_k8s_d, _k8s_b, _k8s_h = _safe_collect_all("kubernetes")
_tok_d, _tok_b, _tok_h = _safe_collect_all("tokenizers")

# LiteLLM: CrewAI Groq routing; PyInstaller often under-collects lazy re-exports.
_lit_d, _lit_b, _lit_h = _safe_collect_all("litellm")

# What-if / LLM stack (lazy-imported but must be present in the frozen app).
_pkg_subs: list[str] = []
for _pkg, _skips in (
    ("crewai", ("crewai.test",)),
    ("instructor", ("instructor.test",)),
    ("langchain_openai", ("langchain_openai.test",)),
    ("langchain_core", ("langchain_core.test",)),
    ("langsmith", ("langsmith.test",)),
    ("litellm", ("litellm.tests", "litellm.test")),
    ("groq", ("groq.test",)),
    ("openai", ("openai.test",)),
    ("uvicorn", ("uvicorn.test",)),
    ("grpc", ("grpc.test",)),
):
    _pkg_subs.extend(_safe_subs(_pkg, _skips))

datas = (
    datas
    + list(_rb_datas)
    + list(_onnx_d)
    + list(_k8s_d)
    + list(_tok_d)
    + list(_lit_d)
)
binaries = (
    list(_rb_binaries)
    + list(_onnx_b)
    + list(_k8s_b)
    + list(_tok_b)
    + list(_lit_b)
)

a = Analysis(
    [str(root / "desktop_main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=(
        [
            "logiclens.config",
            "logiclens.sqlite_graph",
            "logiclens.version",
            "logiclens.updates",
            "logiclens.telemetry",
            "logiclens.chroma_noop_telemetry",
            "waitress",
            "webview",
            "chromadb",
            "chromadb.api.rust",
            "chromadb_rust_bindings",
            "crewai",
            "instructor",
            "langchain_openai",
            "langchain_core",
            "litellm",
            "tiktoken",
            "tiktoken_ext",
            "tiktoken_ext.openai_public",
            "groq",
            "openai",
            "tree_sitter",
            "tree_sitter_python",
            "tree_sitter_javascript",
            "tree_sitter_typescript",
            "tree_sitter_java",
            "tree_sitter_go",
            "tree_sitter_cpp",
            "extractor",
            "whatif_engine",
            "whatif_crew",
            "flask",
            "git",
        ]
        + list(_rb_hidden)
        + list(_onnx_h)
        + list(_k8s_h)
        + list(_tok_h)
        + list(_lit_h)
        + list(_chroma_hidden)
        + list(_pkg_subs)
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LogicLens",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "logo" / "logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LogicLens",
)
