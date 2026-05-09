# PyInstaller spec — run from repo root:
#   pip install pyinstaller
#   pyinstaller packaging/logiclens.spec

import sys
from pathlib import Path

block_cipher = None
root = Path(SPECPATH).resolve().parent

datas = [
    (str(root / "templates"), "templates"),
    (str(root / "static"), "static"),
    (str(root / "logo"), "logo"),
    (str(root / "logiclens"), "logiclens"),
]

a = Analysis(
    [str(root / "desktop_main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "logiclens.config",
        "logiclens.sqlite_graph",
        "waitress",
        "webview",
        "chromadb",
        "crewai",
        "tree_sitter",
        "tree_sitter_python",
        "tree_sitter_javascript",
        "tree_sitter_typescript",
        "tree_sitter_java",
        "tree_sitter_go",
        "tree_sitter_cpp",
        "extractor",
        "whatif_engine",
        "flask",
        "git",
    ],
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
