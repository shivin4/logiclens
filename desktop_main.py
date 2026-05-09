"""
LogicLens desktop entry: Waitress + pywebview (Windows-friendly).

Run: python desktop_main.py
Build: packaging/logiclens.spec (PyInstaller).
"""

from __future__ import annotations

import threading

import webview
from waitress import serve

from logiclens.config import flask_host, flask_port, load_app_env

load_app_env()

from app import app  # noqa: E402


def _serve() -> None:
    serve(app, host=flask_host(), port=flask_port(), threads=6)


def main() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    url = f"http://{flask_host()}:{flask_port()}/"
    webview.create_window("LogicLens", url, width=1320, height=860)
    webview.start()


if __name__ == "__main__":
    main()
