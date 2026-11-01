"""Writes frontend/openapi.json. CI regenerates it and fails on any difference, so the UI's types cannot drift."""

from __future__ import annotations

import json
from pathlib import Path

from gatekeeper.api.app import create_app
from gatekeeper.config import Settings

OUT = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


if __name__ == "__main__":
    spec = create_app(Settings(env="test", demo_mode=True)).openapi()
    OUT.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
