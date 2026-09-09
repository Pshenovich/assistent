#!/usr/bin/env python3
"""Apply USAGE_DASHBOARD_TOKEN from /opt/assistant/.dash_token_sync into .env."""
from __future__ import annotations

from pathlib import Path

ROOT = Path("/opt/assistant")
SRC = ROOT / ".dash_token_sync"
ENV = ROOT / ".env"


def main() -> None:
    tok = SRC.read_text().strip() if SRC.is_file() else ""
    if SRC.is_file():
        SRC.unlink()
    if not ENV.is_file():
        print("dashboard_token:no_env")
        return
    text = ENV.read_text()
    if "USAGE_DASHBOARD_TOKEN=" in text and not any(
        line.startswith("USAGE_DASHBOARD_TOKEN=") for line in text.splitlines()
    ):
        text = text.replace("USAGE_DASHBOARD_TOKEN=", "\nUSAGE_DASHBOARD_TOKEN=", 1)
        ENV.write_text(text)
    lines = ENV.read_text().splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("USAGE_DASHBOARD_TOKEN="):
            found = True
            out.append("USAGE_DASHBOARD_TOKEN=" + tok if tok else line)
        else:
            out.append(line)
    if tok and not found:
        out.append("USAGE_DASHBOARD_TOKEN=" + tok)
    ENV.write_text("\n".join(out).rstrip() + "\n")
    print("dashboard_token:ok" if (tok or found) else "dashboard_token:missing")


if __name__ == "__main__":
    main()
