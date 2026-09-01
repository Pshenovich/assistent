"""Загрузка Notion-страниц для базы знаний."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

_NOTION_VERSION = "2022-06-28"
_PAGE_ID_RE = re.compile(
    r"(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
    re.IGNORECASE,
)


@dataclass
class KbFetchedDocument:
    doc_id: str
    title: str
    text: str
    modified_at: str | None = None
    url: str = ""


def parse_notion_page_id(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    tail = raw.rstrip("/").split("/")[-1]
    tail = tail.split("?")[0].split("#")[0]
    tail = tail.split("-")[-1] if "-" in tail else tail
    m = _PAGE_ID_RE.search(tail.replace("-", ""))
    if not m:
        m = _PAGE_ID_RE.search(raw)
    if not m:
        return None
    pid = m.group(0).replace("-", "")
    if len(pid) != 32:
        return None
    return f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rich_text_to_plain(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        plain = str(item.get("plain_text") or "")
        if plain:
            parts.append(plain)
    return "".join(parts)


def _block_to_lines(block: dict[str, Any]) -> list[str]:
    btype = str(block.get("type") or "")
    payload = block.get(btype) if isinstance(block.get(btype), dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    lines: list[str] = []
    if btype in ("paragraph", "quote", "callout"):
        text = _rich_text_to_plain(payload.get("rich_text"))
        if text.strip():
            lines.append(text.strip())
    elif btype.startswith("heading_"):
        text = _rich_text_to_plain(payload.get("rich_text"))
        if text.strip():
            prefix = "#" * (1 if btype == "heading_1" else 2 if btype == "heading_2" else 3)
            lines.append(f"{prefix} {text.strip()}")
    elif btype in ("bulleted_list_item", "numbered_list_item", "to_do"):
        text = _rich_text_to_plain(payload.get("rich_text"))
        if text.strip():
            lines.append(f"- {text.strip()}")
    elif btype == "code":
        text = _rich_text_to_plain(payload.get("rich_text"))
        if text.strip():
            lines.append(text.strip())
    return lines


def _fetch_block_children(
    token: str,
    block_id: str,
    *,
    timeout: float = 60,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"page_size": "100"}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(
            f"https://api.notion.com/v1/blocks/{block_id}/children",
            headers=_headers(token),
            params=params,
            timeout=timeout,
        )
        if not r.ok:
            raise RuntimeError(f"Notion API: HTTP {r.status_code}: {(r.text or '')[:300]}")
        data = r.json()
        if not isinstance(data, dict):
            break
        results = data.get("results")
        if isinstance(results, list):
            out.extend([x for x in results if isinstance(x, dict)])
        if not data.get("has_more"):
            break
        cursor = str(data.get("next_cursor") or "") or None
        if not cursor:
            break
    return out


def _blocks_to_text(token: str, blocks: list[dict[str, Any]], *, depth: int = 0) -> str:
    lines: list[str] = []
    for block in blocks:
        lines.extend(_block_to_lines(block))
        if block.get("has_children"):
            bid = str(block.get("id") or "")
            if bid:
                children = _fetch_block_children(token, bid)
                child_text = _blocks_to_text(token, children, depth=depth + 1)
                if child_text.strip():
                    lines.append(child_text.strip())
    return "\n".join(lines)


def fetch_notion_page(
    url: str,
    *,
    notion_token: str,
    timeout: float = 60,
) -> KbFetchedDocument:
    token = (notion_token or "").strip()
    if not token:
        raise ValueError("Для Notion нужен Internal Integration Token")
    page_id = parse_notion_page_id(url)
    if not page_id:
        raise ValueError("Не удалось распознать ссылку Notion")
    r = requests.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=_headers(token),
        timeout=timeout,
    )
    if not r.ok:
        raise RuntimeError(f"Notion API: HTTP {r.status_code}: {(r.text or '')[:300]}")
    page = r.json()
    if not isinstance(page, dict):
        raise RuntimeError("Notion API: неожиданный ответ страницы")
    title = page_id
    props = page.get("properties")
    if isinstance(props, dict):
        for val in props.values():
            if not isinstance(val, dict):
                continue
            if val.get("type") == "title":
                title = _rich_text_to_plain(val.get("title")) or title
                break
    blocks = _fetch_block_children(token, page_id, timeout=timeout)
    text = _blocks_to_text(token, blocks).strip()
    if not text:
        raise RuntimeError("Notion: страница пуста или нет доступа у интеграции")
    modified = str(page.get("last_edited_time") or "") or None
    return KbFetchedDocument(
        doc_id=page_id,
        title=title,
        text=text,
        modified_at=modified,
        url=(url or "").strip(),
    )


def fetch_documents(url: str, *, notion_token: str) -> list[KbFetchedDocument]:
    return [fetch_notion_page(url, notion_token=notion_token)]
