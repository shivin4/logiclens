"""
Optional crash reporting (opt-in, privacy-first).

Requires:
  - LOGICLENS_TELEMETRY=1 (or true/yes), and
  - SENTRY_DSN set to a valid Sentry project DSN.

Without a DSN, the telemetry flag does nothing. No data leaves the machine.
Install: pip install sentry-sdk (included in requirements.txt).
"""

from __future__ import annotations

import os
from typing import Any

_initialized = False


def init_flask_telemetry(app: Any) -> None:
    """Call once after Flask ``app`` is created."""
    global _initialized
    if _initialized:
        return
    if os.environ.get("LOGICLENS_TELEMETRY", "").lower() not in ("1", "true", "yes"):
        return
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        return

    from logiclens.version import __version__

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        release=f"logiclens@{__version__}",
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    _initialized = True
