"""Web-canvas полотна: kit, post-process HTML, парсинг leo-canvas блоков из ответа GPT."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

SHEET_KIND_DOC = "doc"
SHEET_KIND_WEB = "web"
KIT_VERSION = "v1"

THEMES = ("editorial", "product-dark", "poster", "soft-light", "brutal")
DEFAULT_THEME = "editorial"

_LEO_CANVAS_RE = re.compile(
    r"```leo-canvas\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_ON_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", re.IGNORECASE | re.DOTALL)
_IFRAME_RE = re.compile(r"<iframe\b[^>]*>.*?</iframe>", re.IGNORECASE | re.DOTALL)
_EXTERNAL_JS_RE = re.compile(
    r"""<script[^>]+src\s*=\s*['"][^'"]+['"][^>]*>\s*</script>""",
    re.IGNORECASE,
)
_PURPLE_GRAD_RE = re.compile(
    r"linear-gradient\([^)]*(?:#7c3aed|#8b5cf6|#6366f1|#a78bfa|purple|indigo)[^)]*\)",
    re.IGNORECASE,
)
_DATA_LEO_ID_RE = re.compile(r"""data-leo-id\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)

_KIT_CSS_CACHE: str | None = None


def normalize_kind(raw: Any) -> str:
    k = str(raw or "").strip().lower()
    if k in (SHEET_KIND_WEB, "canvas", "presentation", "html"):
        return SHEET_KIND_WEB
    return SHEET_KIND_DOC


def normalize_theme(raw: Any) -> str:
    t = str(raw or "").strip().lower().replace("_", "-")
    if t in THEMES:
        return t
    aliases = {
        "dark": "product-dark",
        "product": "product-dark",
        "light": "soft-light",
        "soft": "soft-light",
        "serif": "editorial",
        "type": "poster",
        "mono": "brutal",
    }
    return aliases.get(t, DEFAULT_THEME)


def kit_css_path() -> Path:
    return Path(__file__).resolve().parents[2] / "webapp" / "canvas-kit" / "v1.css"


def load_kit_css() -> str:
    global _KIT_CSS_CACHE
    if _KIT_CSS_CACHE is not None:
        return _KIT_CSS_CACHE
    path = kit_css_path()
    try:
        _KIT_CSS_CACHE = path.read_text(encoding="utf-8")
    except OSError:
        _KIT_CSS_CACHE = "/* kit missing */"
    return _KIT_CSS_CACHE


def canvas_system_addon() -> str:
    themes = ", ".join(THEMES)
    return f"""
## Web-canvas presentations (Leo)
When the user asks to create or edit a web presentation / landing / deck inside the note, respond with a short Russian confirmation AND a fenced block:

```leo-canvas
{{"action":"create","title":"Презентация","theme":"editorial","html":"<!DOCTYPE html>..."}}
```

Rules for the JSON:
- action: "create" (new sheet) or "update" (existing web sheet; include sheet_id)
- title: short sheet title
- theme: one of [{themes}]
- sheet_id: string id when updating an existing web sheet
- html: a full self-contained HTML document
- optional patches: [{{"leo_id":"...","outer_html":"..."}}] for targeted edits (preferred when selections are provided)

HTML / design contract:
- Put data-leo-theme="{{theme}}" on <html>
- Use ONLY Leo Presentation Kit classes: .deck, .slide, .hero, .split, .stack, .rail, .stat, .display, .title, .body, .caption, .cta, .mark, .rule
- Do NOT invent a design system, :root variables, Inter/Roboto, purple/indigo gradients, glow, emoji decoration, pill clusters, or card grids in the hero
- First slide = one composition: brand/mark, one headline, one supporting line, one CTA; one job per slide
- Mark meaningful nodes with unique data-leo-id attributes (hero-title, cta-primary, slide-2-body, …)
- Do not include <script> tags (except none — kit nav is injected by Leo)
- Prefer content from the note context; no lorem ipsum
- Keep HTML reasonably compact (< 120KB)

If the user only chats and does not ask for a presentation, do not emit leo-canvas.
""".strip()


def selections_prompt_block(selections: list[dict[str, Any]] | None) -> str:
    rows = []
    for i, sel in enumerate(selections or []):
        if not isinstance(sel, dict):
            continue
        leo_id = str(sel.get("leo_id") or sel.get("leoId") or "").strip()
        label = str(sel.get("label") or "").strip()
        snippet = str(sel.get("snippet") or "").strip()[:4000]
        sheet_id = str(sel.get("sheet_id") or sel.get("sheetId") or "").strip()
        if not leo_id and not snippet:
            continue
        rows.append(
            f"- [{i + 1}] sheet_id={sheet_id or '—'} leo_id={leo_id or '—'} "
            f"label={label or '—'}\\n  snippet:\\n{snippet}"
        )
    if not rows:
        return ""
    return (
        "Selected web-canvas elements (edit these preferentially via patches "
        "or update with the same data-leo-id):\n" + "\n".join(rows)
    )


def parse_leo_canvas_blocks(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip ```leo-canvas``` blocks from answer; return (clean_answer, actions)."""
    actions: list[dict[str, Any]] = []
    if not text:
        return "", actions

    def _repl(match: re.Match[str]) -> str:
        raw = (match.group(1) or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # try to recover truncated JSON loosely
            return match.group(0)
        if isinstance(data, dict):
            actions.append(data)
            return ""
        return match.group(0)

    cleaned = _LEO_CANVAS_RE.sub(_repl, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, actions


def _annotate_missing_ids(html: str) -> str:
    """Insert data-leo-id on key tags that lack one."""
    used = {m.group(1) for m in _DATA_LEO_ID_RE.finditer(html)}
    counter = [1]
    out: list[str] = []
    i = 0
    pattern = re.compile(
        r"<(section|article|header|footer|h[1-6]|p|button|a|figure|nav|div)(\s[^>]*)?>",
        re.IGNORECASE,
    )
    while i < len(html):
        m = pattern.search(html, i)
        if not m:
            out.append(html[i:])
            break
        out.append(html[i : m.start()])
        full = m.group(0)
        attrs = m.group(2) or ""
        if re.search(r"data-leo-id\s*=", attrs, re.IGNORECASE):
            out.append(full)
        else:
            while True:
                cand = f"el-{counter[0]}"
                counter[0] += 1
                if cand not in used:
                    used.add(cand)
                    break
            name = m.group(1)
            out.append(f'<{name}{attrs} data-leo-id="{cand}">')
        i = m.end()
    return "".join(out)


def strip_slop(html: str) -> str:
    html = _EXTERNAL_JS_RE.sub("", html)
    html = _SCRIPT_RE.sub("", html)
    html = _IFRAME_RE.sub("", html)
    html = _ON_ATTR_RE.sub("", html)
    html = _PURPLE_GRAD_RE.sub("var(--accent-soft)", html)
    return html


def apply_patches(html: str, patches: list[dict[str, Any]] | None) -> str:
    doc = html or ""
    for patch in patches or []:
        if not isinstance(patch, dict):
            continue
        leo_id = str(patch.get("leo_id") or patch.get("leoId") or "").strip()
        outer = str(patch.get("outer_html") or patch.get("outerHtml") or "").strip()
        if not leo_id or not outer:
            continue
        # Replace element with matching data-leo-id (non-greedy until matching close is hard;
        # use a pragmatic regex for a single element start→end with same tag).
        esc = re.escape(leo_id)
        pattern = re.compile(
            rf'<(?P<tag>[a-zA-Z0-9]+)([^>]*\sdata-leo-id=["\']{esc}["\'][^>]*)>.*?</(?P=tag)>',
            re.IGNORECASE | re.DOTALL,
        )
        if pattern.search(doc):
            doc = pattern.sub(outer, doc, count=1)
        else:
            # fallback: replace opening tag region only
            open_pat = re.compile(
                rf'<([a-zA-Z0-9]+)([^>]*\sdata-leo-id=["\']{esc}["\'][^>]*)>',
                re.IGNORECASE,
            )
            m = open_pat.search(doc)
            if m:
                # if outer is a full element, splice from open to approximate end
                doc = open_pat.sub(outer, doc, count=1)
    return doc


def finalize_canvas_html(
    html: str,
    *,
    theme: str | None = None,
    patches: list[dict[str, Any]] | None = None,
) -> str:
    theme_n = normalize_theme(theme)
    raw = (html or "").strip()
    if patches:
        raw = apply_patches(raw, patches)
    raw = strip_slop(raw)
    if not raw:
        raw = (
            f'<!DOCTYPE html><html data-leo-theme="{theme_n}"><head><meta charset="utf-8">'
            f"<title>Презентация</title></head><body><main class=\"deck\">"
            f'<section class="slide hero" data-leo-id="slide-1">'
            f'<p class="mark" data-leo-id="brand">Leo</p>'
            f'<h1 class="display" data-leo-id="hero-title">Презентация</h1>'
            f'<p class="body" data-leo-id="hero-sub">Добавьте содержание в обсуждении.</p>'
            f"</section></main></body></html>"
        )
    if "<html" not in raw.lower():
        raw = (
            f'<!DOCTYPE html><html data-leo-theme="{theme_n}"><head><meta charset="utf-8">'
            f"<title>Презентация</title></head><body class=\"deck\">{raw}</body></html>"
        )
    # Force theme attr
    if re.search(r"<html\b", raw, re.IGNORECASE):
        if re.search(r"data-leo-theme\s*=", raw, re.IGNORECASE):
            raw = re.sub(
                r'data-leo-theme\s*=\s*[\'"][^\'"]*[\'"]',
                f'data-leo-theme="{theme_n}"',
                raw,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            raw = re.sub(
                r"<html\b",
                f'<html data-leo-theme="{theme_n}"',
                raw,
                count=1,
                flags=re.IGNORECASE,
            )
    raw = _annotate_missing_ids(raw)
    css = load_kit_css()
    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link id="leo-canvas-fonts" href="https://fonts.googleapis.com/css2?'
        "family=Fraunces:opsz,wght@9..144,500;9..144,700&"
        "family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&"
        "family=IBM+Plex+Sans:wght@400;500;600&"
        "family=IBM+Plex+Mono:wght@400;500&"
        "family=Syne:wght@600;700;800&"
        "family=Newsreader:opsz,wght@6..72,400;6..72,600&"
        "family=Space+Grotesk:wght@500;600;700&display=swap\" rel=\"stylesheet\">"
    )
    style_tag = (
        f'{fonts}<style id="leo-canvas-kit" data-kit="{KIT_VERSION}">\n{css}\n</style>'
    )
    # Remove previous kit inject
    raw = re.sub(
        r'<style[^>]*id=["\']leo-canvas-kit["\'][^>]*>.*?</style>',
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    raw = re.sub(
        r'<link[^>]*id=["\']leo-canvas-fonts["\'][^>]*>',
        "",
        raw,
        flags=re.IGNORECASE,
    )
    if re.search(r"</head>", raw, re.IGNORECASE):
        raw = re.sub(r"</head>", style_tag + "</head>", raw, count=1, flags=re.IGNORECASE)
    else:
        raw = style_tag + raw
    # Cap size
    if len(raw) > 200_000:
        raw = raw[:200_000]
    return raw


def extract_theme_from_html(html: str) -> str:
    m = re.search(r'data-leo-theme\s*=\s*[\'"]([^\'"]+)[\'"]', html or "", re.IGNORECASE)
    return normalize_theme(m.group(1) if m else DEFAULT_THEME)


def apply_canvas_action(
    *,
    note: dict[str, Any],
    action: dict[str, Any],
    note_sheets_mod: Any,
) -> Optional[dict[str, Any]]:
    """Create or update a web sheet from a parsed leo-canvas action. Returns sheet dict."""
    act = str(action.get("action") or "create").strip().lower()
    title = str(action.get("title") or "").strip() or "Презентация"
    theme = normalize_theme(action.get("theme"))
    html_in = str(action.get("html") or "")
    patches = action.get("patches") if isinstance(action.get("patches"), list) else None
    sheet_id_raw = action.get("sheet_id") or action.get("sheetId")

    if act == "update" and sheet_id_raw is not None and str(sheet_id_raw).strip():
        try:
            sid = int(sheet_id_raw)
        except (TypeError, ValueError):
            return None
        existing = note_sheets_mod.get_extra_sheet(int(note["id"]), sid)
        if not existing:
            return None
        if normalize_kind(existing.get("kind")) != SHEET_KIND_WEB:
            return None
        base_html = html_in if html_in.strip() else str(existing.get("body") or "")
        if not theme and base_html:
            theme = extract_theme_from_html(base_html)
        finalized = finalize_canvas_html(base_html, theme=theme, patches=patches)
        return note_sheets_mod.update_sheet(
            int(note["id"]),
            sid,
            title=title if action.get("title") else None,
            body=finalized,
            kind=SHEET_KIND_WEB,
        )

    finalized = finalize_canvas_html(html_in, theme=theme, patches=patches)
    return note_sheets_mod.create_sheet(
        note,
        title=title,
        body=finalized,
        kind=SHEET_KIND_WEB,
    )
