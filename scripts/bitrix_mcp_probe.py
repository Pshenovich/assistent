"""Печатает инструменты Bitrix24 MCP со схемами аргументов (только чтение).

Использование: python scripts/bitrix_mcp_probe.py [путь к json-файлу токена]
Без аргумента берёт BITRIX_MCP_TOKEN из .env.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from assistant.integrations.bitrix_mcp_client import list_tools  # noqa: E402
from assistant.integrations.bitrix_mcp_token import env_mcp_token  # noqa: E402


def _token() -> str:
    if len(sys.argv) > 1:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        return str(data.get("token") or "")
    return env_mcp_token() or ""


def main() -> None:
    token = _token()
    if not token:
        sys.exit("Нет токена Bitrix24 MCP")
    tools = asyncio.run(list_tools(token))
    for tool in tools:
        props = (getattr(tool, "inputSchema", None) or {}).get("properties") or {}
        print(f"{tool.name}: {', '.join(sorted(props))}")
        desc = (getattr(tool, "description", "") or "").strip().splitlines()
        if desc:
            print(f"    {desc[0][:160]}")


if __name__ == "__main__":
    main()
