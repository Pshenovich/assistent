"""Загрузка базы знаний Bitrix24 (landing API, scope KNOWLEDGE)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from assistant.integrations.bitrix24_rest import webhook_base
from assistant.integrations.bitrix_mcp_portal import portal_host_from_token
from assistant.integrations.bitrix_mcp_token import get_user_token
from assistant.lib.telegram_html import html_to_plain
from assistant.lib.kb_html_format import html_to_kb_markdown

_KNOWLEDGE_URL_RE = re.compile(
    r"^https?://([^/]+)/knowledge/([a-zA-Z0-9_-]+)(?:/([a-zA-Z0-9_-]+))?/?",
    re.IGNORECASE,
)
_BITRIX_HOST_RE = re.compile(r"bitrix24\.(ru|com|de|es|fr|pl|ua|by|kz)", re.IGNORECASE)


@dataclass
class KbFetchedDocument:
    doc_id: str
    title: str
    text: str
    modified_at: str | None = None
    url: str = ""


@dataclass(frozen=True)
class BitrixKnowledgeRef:
    portal_host: str
    kb_code: str
    page_code: str | None = None


def is_bitrix_knowledge_url(url: str) -> bool:
    return parse_bitrix_knowledge_url(url) is not None


def parse_bitrix_knowledge_url(url: str) -> BitrixKnowledgeRef | None:
    raw = (url or "").strip()
    if not raw:
        return None
    m = _KNOWLEDGE_URL_RE.match(raw)
    if not m:
        return None
    host = m.group(1).strip().lower()
    if not _BITRIX_HOST_RE.search(host):
        return None
    kb_code = (m.group(2) or "").strip()
    page_code = (m.group(3) or "").strip() or None
    if not kb_code:
        return None
    return BitrixKnowledgeRef(
        portal_host=host,
        kb_code=kb_code,
        page_code=page_code,
    )


def _portal_hosts_match(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _webhook_host(webhook_url: str) -> str:
    return (urlparse(webhook_url).netloc or "").strip().lower()


def resolve_rest_webhook(
    *,
    portal_host: str,
    owner_telegram_user_id: int,
    source_meta: dict[str, Any] | None = None,
) -> str:
    meta = source_meta if isinstance(source_meta, dict) else {}
    custom = str(meta.get("bitrix_webhook_url") or "").strip().rstrip("/")
    if custom:
        return custom

    env_hook = webhook_base()
    if env_hook and _portal_hosts_match(_webhook_host(env_hook), portal_host):
        return env_hook

    token = get_user_token(int(owner_telegram_user_id))
    if token:
        mcp_portal = urlparse(portal_host_from_token(token)).netloc.lower()
        if _portal_hosts_match(mcp_portal, portal_host) and env_hook:
            return env_hook

    raise RuntimeError(
        "Для базы знаний Битрикс24 нужен входящий вебхук REST "
        f"(BITRIX24_WEBHOOK_URL) для портала {portal_host} с правами landing. "
        "Подключите Битрикс24 в профиле (MCP) и убедитесь, что вебхук настроен на сервере."
    )


def _rest_post(webhook: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = (webhook or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Не задан вебхук Bitrix24 REST")
    try:
        r = requests.post(f"{base}/{method}", json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Bitrix24 REST: {e}") from e
    except ValueError as e:
        raise RuntimeError("Bitrix24 REST: ответ не JSON") from e
    if not isinstance(data, dict):
        raise RuntimeError("Bitrix24 REST: неожиданный ответ")
    if data.get("error"):
        err = str(data.get("error_description") or data.get("error") or "unknown")
        raise RuntimeError(f"Bitrix24 REST: {err}")
    return data


def _rest_collect(webhook: str, method: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start = 0
    body = dict(payload)
    while True:
        page_body = dict(body)
        params = dict(page_body.get("params") or {})
        params.pop("start", None)
        page_body["params"] = params
        page_body["start"] = start
        data = _rest_post(webhook, method, page_body)
        result = data.get("result")
        if isinstance(result, list):
            out.extend([x for x in result if isinstance(x, dict)])
        elif isinstance(result, dict):
            items = result.get("items") or result.get("list")
            if isinstance(items, list):
                out.extend([x for x in items if isinstance(x, dict)])
        nxt = data.get("next")
        if nxt is None:
            break
        try:
            start = int(nxt)
        except (TypeError, ValueError):
            break
        if start <= 0:
            break
    return out


def _kb_site_code_variants(kb_code: str) -> list[str]:
    raw = (kb_code or "").strip().strip("/")
    if not raw:
        return []
    variants = [raw, f"/{raw}/", f"/{raw}", raw.strip("/")]
    out: list[str] = []
    for item in variants:
        if item and item not in out:
            out.append(item)
    return out


def _normalize_kb_site_code(value: str) -> str:
    return (value or "").strip().strip("/").lower()


def _find_knowledge_site(
    webhook: str, *, kb_code: str
) -> dict[str, Any]:
    want = _normalize_kb_site_code(kb_code)
    for code in _kb_site_code_variants(kb_code):
        for op in ("CODE", "=CODE"):
            sites = _rest_collect(
                webhook,
                "landing.site.getList",
                {
                    "scope": "KNOWLEDGE",
                    "params": {
                        "filter": {op: code, "TYPE": "KNOWLEDGE"},
                        "select": ["ID", "TITLE", "CODE", "DATE_MODIFY", "TYPE"],
                    },
                },
            )
            if sites:
                return sites[0]
    sites = _rest_collect(
        webhook,
        "landing.site.getList",
        {
            "scope": "KNOWLEDGE",
            "params": {
                "filter": {"TYPE": "KNOWLEDGE"},
                "select": ["ID", "TITLE", "CODE", "DATE_MODIFY", "TYPE"],
            },
        },
    )
    for site in sites:
        code = _normalize_kb_site_code(str(site.get("CODE") or site.get("code") or ""))
        if code == want:
            return site
    if not sites:
        raise RuntimeError(
            f"База знаний «{kb_code}» не найдена на портале или нет прав landing"
        )
    raise RuntimeError(
        f"База знаний «{kb_code}» не найдена на портале или нет прав landing"
    )


def _list_site_pages(webhook: str, *, site_id: int) -> list[dict[str, Any]]:
    return _rest_collect(
        webhook,
        "landing.landing.getList",
        {
            "scope": "KNOWLEDGE",
            "params": {
                "filter": {"SITE_ID": int(site_id)},
                "select": ["ID", "TITLE", "CODE", "DATE_MODIFY", "SITE_ID"],
            },
        },
    )


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_html = str(block.get("content") or "").strip()
        if not raw_html:
            continue
        plain = html_to_kb_markdown(raw_html).strip() or html_to_plain(raw_html).strip()
        if plain:
            parts.append(plain)
    return "\n\n".join(parts).strip()


def _fetch_page_blocks(webhook: str, *, lid: int) -> list[dict[str, Any]]:
    for edit_mode in (False, True):
        for method in ("landing.block.getlist", "landing.block.getList"):
            blocks = _rest_collect(
                webhook,
                method,
                {
                    "scope": "KNOWLEDGE",
                    "lid": lid,
                    "params": {"get_content": True, "edit_mode": edit_mode},
                },
            )
            if blocks:
                return blocks
    return []


def _fetch_page_document(
    webhook: str,
    page: dict[str, Any],
    *,
    portal_host: str,
    kb_code: str,
) -> KbFetchedDocument | None:
    page_id = page.get("ID") or page.get("id")
    if page_id is None:
        return None
    try:
        lid = int(page_id)
    except (TypeError, ValueError):
        return None
    title = str(page.get("TITLE") or page.get("title") or f"page-{lid}").strip()
    code = str(page.get("CODE") or page.get("code") or lid).strip()
    modified = str(page.get("DATE_MODIFY") or page.get("date_modify") or "") or None

    blocks = _fetch_page_blocks(webhook, lid=lid)
    text = _blocks_to_text(blocks)
    if not text:
        return None
    doc_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", code)[:120] or str(lid)
    page_url = f"https://{portal_host}/knowledge/{kb_code}/{code}/"
    return KbFetchedDocument(
        doc_id=doc_id,
        title=title,
        text=text,
        modified_at=modified,
        url=page_url,
    )


def fetch_documents(
    url: str,
    *,
    owner_telegram_user_id: int,
    source_meta: dict[str, Any] | None = None,
) -> list[KbFetchedDocument]:
    ref = parse_bitrix_knowledge_url(url)
    if not ref:
        raise ValueError("Не удалось распознать ссылку на базу знаний Битрикс24")
    webhook = resolve_rest_webhook(
        portal_host=ref.portal_host,
        owner_telegram_user_id=int(owner_telegram_user_id),
        source_meta=source_meta,
    )
    site = _find_knowledge_site(webhook, kb_code=ref.kb_code)
    site_id = site.get("ID") or site.get("id")
    if site_id is None:
        raise RuntimeError("Bitrix24: у базы знаний нет ID")
    pages = _list_site_pages(webhook, site_id=int(site_id))
    if ref.page_code:
        pages = [
            p
            for p in pages
            if str(p.get("CODE") or p.get("code") or "").strip().lower()
            == ref.page_code.lower()
        ]
        if not pages:
            raise RuntimeError(f"Страница «{ref.page_code}» не найдена в базе знаний")
    docs: list[KbFetchedDocument] = []
    for page in pages:
        doc = _fetch_page_document(
            webhook,
            page,
            portal_host=ref.portal_host,
            kb_code=ref.kb_code,
        )
        if doc:
            docs.append(doc)
    if not docs:
        raise RuntimeError(
            "В базе знаний Битрикс24 нет текстового контента для индексации"
        )
    return docs
