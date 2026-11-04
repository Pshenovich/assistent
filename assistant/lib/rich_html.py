"""Очистка HTML документов и саммари перед отдачей наружу (страница /share/ и её JSON).

Белый список nh3: разметка редактора (заголовки, списки, чеклисты, таблицы, цвета,
data-leo-*) остаётся; скрипты, обработчики on*, javascript:/data: в ссылках, url() в стилях — нет.
Тот же белый список на клиенте — NoteHtml.sanitizeHtml (webapp/note-html.js).
"""

from __future__ import annotations

import re
from typing import Any

import nh3

TAGS = {
    "p", "br", "div", "span", "b", "strong", "i", "em", "u", "s", "strike", "del", "ins", "mark", "small",
    "sub", "sup", "code", "pre", "kbd", "blockquote", "hr", "a", "img", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "label", "input", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "colgroup",
    "col", "caption", "figure", "figcaption", "details", "summary",
}
CLEAN_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template", "noscript", "textarea", "select"}
ATTRS = {
    "*": {"class", "title", "style", "lang", "dir"},
    "a": {"href", "target"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "colwidth", "align"},
    "th": {"colspan", "rowspan", "colwidth", "align"},
    "col": {"width", "span"},
    "ol": {"start", "type"},
    "input": {"checked", "disabled"},
    "details": {"open"},
}
STYLE_PROPS = {
    "color", "background-color", "font-size", "font-weight", "font-style", "font-family", "text-align",
    "text-decoration", "line-height", "vertical-align", "white-space", "width", "min-width", "max-width",
    "height", "padding", "padding-left", "margin-left", "border", "border-color", "border-collapse",
}
_HTML_RE = re.compile(r"</?[a-z][\s\S]*>", re.I)
_CTRL_RE = re.compile(r"[\x00-\x20\x7f-\x9f]")
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.I)


def _attr_filter(tag: str, attr: str, value: str) -> Any:
    if attr in ("href", "src"):
        v = _CTRL_RE.sub("", value or "")
        if tag == "img" and attr == "src" and re.match(r"(?i)^data:image/(png|jpe?g|gif|webp);", v):
            return value
        m = _SCHEME_RE.match(v)
        if not m:
            return value  # относительный адрес или #якорь
        scheme = m.group(1).lower()
        if scheme in ("http", "https") or (attr == "href" and scheme in ("mailto", "tel", "tg")):
            return value
        return None
    return value


def looks_like_html(text: str) -> bool:
    """То же правило, что looksLikeHtml в webapp/share.js: иначе клиент рендерит текст как Markdown."""
    return bool(_HTML_RE.search(text or ""))


def sanitize_rich_html(text: str) -> str:
    """HTML — через белый список; Markdown и обычный текст не трогаем (клиент их сам экранирует)."""
    raw = str(text or "")
    if not looks_like_html(raw):
        return raw
    return nh3.clean(
        raw,
        tags=TAGS,
        clean_content_tags=CLEAN_CONTENT_TAGS,
        attributes=ATTRS,
        generic_attribute_prefixes={"data-", "aria-"},
        attribute_filter=_attr_filter,
        url_schemes={"http", "https", "mailto", "tel", "tg", "data"},
        filter_style_properties=STYLE_PROPS,
        tag_attribute_values={"input": {"type": {"checkbox"}}},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
