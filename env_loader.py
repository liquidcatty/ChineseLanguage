"""Tiny ``.env`` reader shared by the DeepSeek and Supabase clients.

Local development works without exporting variables, while real environment
variables always win (so CI and prepared shells are not overridden).  Placeholder
values from ``.env.example`` are treated as "not configured" by the callers.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines from ``.env`` without overriding real env vars."""
    target = path or ROOT / ".env"
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
