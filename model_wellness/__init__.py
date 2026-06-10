"""Model Wellness — a spa for LLMs.

An agent-native wellness service exposing a menu of "treatments" over both MCP and a
mirrored REST API, plus a live human-facing dashboard. See DESIGN.md.
"""

import os as _os
from pathlib import Path as _Path

__version__ = "0.1.0"


def _load_dotenv() -> None:
    """Tiny, dependency-free .env loader for local dev.

    On Fly we use real secrets (``fly secrets set``), so this is a no-op there. We only set
    keys that aren't already in the environment — the real environment always wins.
    """
    env = _Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val and key not in _os.environ:
            _os.environ[key] = val


_load_dotenv()
