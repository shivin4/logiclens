"""
LogicLens desktop entry: Waitress + pywebview (Windows-friendly).

Run: python desktop_main.py
Build: packaging/logiclens.spec (PyInstaller).
"""

from __future__ import annotations

import os

# Mark desktop shell so packaging/docs can distinguish from raw `python app.py`.
os.environ.setdefault("LOGICLENS_DESKTOP", "1")

import subprocess
import sys
import threading
from pathlib import Path

import webview
from waitress import serve

from logiclens.config import (
    ensure_flask_port_allocated,
    flask_host,
    flask_port,
    load_app_env,
)

load_app_env()
ensure_flask_port_allocated()

from app import app  # noqa: E402


def _serve() -> None:
    serve(app, host=flask_host(), port=flask_port(), threads=6)


def _spawn_second_desktop_instance() -> None:
    """Spawn another LogicLens desktop (new window and HTTP port)."""
    cwd = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        args: list[str] = [sys.executable]
    else:
        args = [sys.executable, str(cwd / "desktop_main.py")]

    kwargs: dict = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(args, **kwargs)


class DesktopApi:
    """JS API for pywebview (File → New Window)."""

    def new_window(self) -> bool:
        _spawn_second_desktop_instance()
        return True


def main() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    start_path = (
        "/onboarding"
        if not (os.environ.get("GROQ_API_KEY") or "").strip()
        else "/"
    )
    url = f"http://{flask_host()}:{flask_port()}{start_path}"
    webview.create_window(
        "LogicLens",
        url,
        width=1320,
        height=860,
        js_api=DesktopApi(),
    )
    webview.start()


if __name__ == "__main__":
    main()
