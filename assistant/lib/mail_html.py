"""Безопасный и красивый HTML писем для Orbita.

- Белый список тегов и атрибутов (nh3): без скриптов, стилей, форм, обработчиков событий.
- Внешние картинки не грузятся (трекинг-пиксели): src → data-src, клиент покажет их по «Показать картинки».
- Встроенные картинки (cid:) подставляются data-URI из вложений письма.
- Для вида «чат» — без хвоста цитаты (он уже есть пузырями выше); полный вариант — для «Показать целиком».
- Подпись (gmail_signature) помечается классом mail-sig — клиент сворачивает её в «…».
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Any

import nh3

INLINE_IMAGE_MAX = 512 * 1024

TAGS = {
    "p", "br", "div", "span", "b", "strong", "i", "em", "u", "s", "strike", "a", "ul", "ol", "li", "blockquote",
    "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "img",
    "hr", "sub", "sup", "small", "font", "center",
}
ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "data-src", "alt", "width", "height", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "div": {"class"},
    "span": {"class"},
    "blockquote": {"class", "type"},
}
# «Как в почте»: для писем с вёрсткой (таблицы, свои цвета) оставляем безопасные стили и показываем их на белом листе.
STYLE_PROPS = {
    "color", "background-color", "font-size", "font-weight", "font-style", "font-family", "text-align",
    "text-decoration", "text-transform", "line-height", "letter-spacing", "vertical-align", "white-space",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "width", "max-width", "min-width", "height", "border", "border-top", "border-right", "border-bottom",
    "border-left", "border-color", "border-width", "border-style", "border-radius", "border-collapse",
    "border-spacing", "display", "table-layout",
}  # без background / background-image: через url() грузятся трекинг-пиксели
RICH_ATTRS = {
    "table": {"width", "cellpadding", "cellspacing", "border", "align", "bgcolor", "style"},
    "td": {"colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "style"},
    "th": {"colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "style"},
    "tr": {"align", "valign", "bgcolor", "style"},
    "font": {"color", "size", "face", "style"},
}
_RICH_RE = re.compile(r"(?is)<table\b|bgcolor\s*=|style\s*=\s*[\"'][^\"']*background")

_QUOTE_CLASSES = ("gmail_quote", "yahoo_quoted", "moz-cite-prefix", "protonmail_quote", "zmail_extra")
_SIG_CLASSES = ("gmail_signature", "signature", "moz-signature")
_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def _attr_filter(tag: str, attr: str, value: str) -> Any:
    if attr == "href":
        v = value.strip()
        return v if re.match(r"(?i)^(https?:|mailto:|tel:)", v) else None
    if tag == "img" and attr == "src":
        return value if value.startswith("data:image/") else None
    if tag == "img" and attr == "data-src":
        return value if re.match(r"(?i)^https?://", value) else None
    if attr == "class":
        low = value.lower()
        if any(c in low for c in _QUOTE_CLASSES):
            return "mail-quote"
        if any(c in low for c in _SIG_CLASSES):
            return "mail-sig"
        return None
    if attr == "type":
        return "cite" if value.lower() == "cite" else None
    return value


def _inline_images(msg: Message) -> dict[str, str]:
    """cid → data-URI для встроенных картинок письма."""
    out: dict[str, str] = {}
    for part in msg.walk():
        cid = str(part.get("Content-ID") or "").strip().strip("<>")
        ctype = (part.get_content_type() or "").lower()
        if not cid or not ctype.startswith("image/"):
            continue
        data = part.get_payload(decode=True) or b""
        if 0 < len(data) <= INLINE_IMAGE_MAX:
            out[cid] = f"data:{ctype};base64," + base64.b64encode(data).decode()
    return out


def _prepare(raw: str, cids: dict[str, str]) -> tuple[str, bool]:
    """До очистки: cid → data-URI, внешние картинки → data-src (не грузятся без спроса)."""
    remote = False

    def img(m: re.Match) -> str:
        nonlocal remote
        q, url = m.group(2), m.group(3)
        if url.lower().startswith("cid:"):
            data = cids.get(url[4:].strip("<>"))
            return f"{m.group(1)} src={q}{data}{q}" if data else m.group(1)
        if re.match(r"(?i)https?://", url):
            remote = True
            return f"{m.group(1)} data-src={q}{url}{q}"
        return m.group(0)

    raw = re.sub(r"(<img\b[^>]*?)\s+src\s*=\s*([\"'])([^\"']*)\2", img, raw, flags=re.I)
    raw = re.sub(r"\s+srcset\s*=\s*([\"'])[^\"']*\1", "", raw, flags=re.I)
    return raw, remote


class _DropQuotes(HTMLParser):
    """Убирает цитату: элементы mail-quote и blockquote (и всё внутри)."""

    VOID = {"br", "img", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Any]]) -> None:
        a = dict(attrs)
        quote = tag == "blockquote" or "mail-quote" in str(a.get("class") or "")
        if self.skip or quote:
            if tag not in self.VOID:
                self.skip += 1
            return
        self.out.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Any]]) -> None:
        if not self.skip:
            self.out.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag not in self.VOID:
                self.skip -= 1
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.skip:
            self.out.append(f"&#{name};")


_ATTRIBUTION_RE = re.compile(
    r"(?is)(<(?:div|p)[^>]*>\s*)?(?:On [^<]{5,200} wrote:|[^<]{0,200}(?:пишет|написал|написала)\s*:)\s*(</(?:div|p)>)?\s*$"
)


def drop_quotes(clean_html: str) -> str:
    p = _DropQuotes()
    p.feed(clean_html)
    p.close()
    out = "".join(p.out)
    out = _ATTRIBUTION_RE.sub("", out.rstrip())  # «22.10.2026, Иван пишет:» перед убранной цитатой
    return re.sub(r"(?:\s*<br\s*/?>\s*|\s*<div>\s*(?:<br\s*/?>)?\s*</div>\s*)+$", "", out).strip()


def text_to_html(text: str) -> str:
    """Простое текстовое письмо → абзацы, переносы, ссылки."""
    parts = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        esc = html_lib.escape(para)
        esc = _URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', esc)
        parts.append("<p>" + esc.replace("\n", "<br>") + "</p>")
    return "".join(parts)


def _html_part(msg: Message) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart() or "attachment" in str(part.get("Content-Disposition") or "").lower():
            continue
        if (part.get_content_type() or "").lower() == "text/html":
            payload = part.get_payload(decode=True) or b""
            try:
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                return payload.decode("utf-8", errors="replace")
    return ""


def is_rich(raw_html: str) -> bool:
    """Письмо со своей вёрсткой (таблицы, фоны) — показываем «как в почте», на белом листе."""
    return bool(_RICH_RE.search(raw_html or ""))


def clean(raw_html: str, cids: dict[str, str] | None = None, *, rich: bool = False) -> tuple[str, bool]:
    prepared, remote = _prepare(raw_html or "", cids or {})
    attrs = ATTRS
    if rich:
        attrs = {k: set(v) for k, v in ATTRS.items()}
        for tag, extra in RICH_ATTRS.items():
            attrs[tag] = attrs.get(tag, set()) | extra
        for tag in TAGS:
            attrs.setdefault(tag, set()).add("style")
    out = nh3.clean(
        prepared, tags=TAGS, attributes=attrs, url_schemes={"http", "https", "mailto", "tel", "data"},
        attribute_filter=_attr_filter, link_rel="noopener noreferrer nofollow", strip_comments=True,
        clean_content_tags={"script", "style", "title", "head"},
        filter_style_properties=STYLE_PROPS if rich else None,
    )
    out = re.sub(r"<a\s", '<a target="_blank" ', out)
    return out, remote


def render(msg: Message, plain_author_text: str) -> dict[str, Any]:
    """{html — без цитаты (чат), html_full — целиком, remote_images — есть ли внешние картинки}."""
    from assistant.integrations import mail_imap

    raw = _html_part(msg)
    if raw:
        rich = is_rich(raw)
        full, remote = clean(raw, _inline_images(msg), rich=rich)
        short = drop_quotes(full)
        if not re.sub(r"<[^>]+>|&nbsp;|\s", "", short):
            short = text_to_html(plain_author_text)
        return {"html": short, "html_full": full, "remote_images": remote, "rich": rich}
    text = mail_imap.body_text(msg)
    return {"html": text_to_html(plain_author_text), "html_full": text_to_html(text), "remote_images": False, "rich": False}
