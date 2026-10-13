"""Web-canvas полотна: kit, post-process HTML, парсинг leo-canvas блоков из ответа GPT."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

SHEET_KIND_DOC = "doc"
SHEET_KIND_WEB = "web"
KIT_VERSION = "v1"

THEMES = ("bento", "editorial", "product-dark", "poster", "soft-light", "brutal")
DEFAULT_THEME = "bento"

_LEO_CANVAS_RE = re.compile(
    r"```(?:leo-canvas|json|javascript)?\s*\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_OPEN_RE = re.compile(r"```(?:leo-canvas|json|javascript)?\s*\n?", re.IGNORECASE)
_BARE_ACTION_RE = re.compile(
    r"\{[^{}]*\"action\"\s*:\s*\"(?:create|update)\"",
    re.IGNORECASE,
)
_ACTION_KEY_RE = re.compile(r'"action"\s*:\s*"(?:create|update)"', re.IGNORECASE)
_CANVAS_CLAIM_RE = re.compile(
    r"(готово|собрал|собрала|обновил|обновила|поправил|поправила|"
    r"сделал|сделала|нарисовал|нарисовала).{0,48}"
    r"(презентац|полотн|слайд|deck|canvas)",
    re.IGNORECASE | re.DOTALL,
)
CANVAS_FAIL_MSG = (
    "Не удалось собрать полотно — ответ модели оборвался или не распознан. "
    "Попробуйте ещё раз короче (4–6 слайдов)."
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
_DATA_LEO_IMG_PROMPT_RE = re.compile(
    r"""data-leo-img-prompt\s*=\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_PENDING_MEDIA_SLOT_RE = re.compile(
    r"""<(div|figure|span)(\s[^>]*?\b(?:data-leo-img-prompt\s*=\s*['"][^'"]+['"]|data-leo-img\s*=\s*['"]pending['"])[^>]*)>(.*?)</\1>""",
    re.IGNORECASE | re.DOTALL,
)
_ABSTRACT_SLOT_RE = re.compile(
    r"""<(div|figure)(\s[^>]*?\babstract\b[^>]*)>(.*?)</\1>""",
    re.IGNORECASE | re.DOTALL,
)

_KIT_CSS_CACHE: str | None = None


def image_dicts_to_data_urls(images: list[dict[str, str]] | None) -> list[str]:
    """Convert openrouter image dicts ({mime,b64}) to data URLs."""
    out: list[str] = []
    for img in images or []:
        if not isinstance(img, dict):
            continue
        b64 = str(img.get("b64") or "").strip()
        mime = str(img.get("mime") or "image/png").split(";")[0].strip() or "image/png"
        if not b64 or len(b64) > 1_800_000:
            continue
        out.append(f"data:{mime};base64,{b64}")
    return out


def extract_canvas_img_prompts(html: str) -> list[str]:
    """data-leo-img-prompt values in document order."""
    prompts: list[str] = []
    for m in _DATA_LEO_IMG_PROMPT_RE.finditer(html or ""):
        p = str(m.group(1) or "").strip()
        if p:
            prompts.append(p[:2000])
    return prompts


def _leo_id_from_attrs(attrs: str, fallback: str) -> str:
    m = _DATA_LEO_ID_RE.search(attrs or "")
    return str(m.group(1)).strip() if m else fallback


def _strip_media_pending_attrs(attrs: str) -> str:
    cleaned = re.sub(
        r"""\s*data-leo-img-prompt\s*=\s*['"][^'"]*['"]""",
        "",
        attrs or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"""\s*data-leo-img\s*=\s*['"][^'"]*['"]""",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    if re.search(r"""\bclass\s*=\s*['"][^'"]*\bmedia-slot\b""", cleaned, re.I):
        return cleaned
    if re.search(r"""\bclass\s*=\s*['"]""", cleaned, re.I):
        return re.sub(
            r"""\bclass\s*=\s*(['"])""",
            r"class=\1media-slot ",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
    return f'{cleaned} class="media-slot"'


def fill_media_slots_with_data_urls(html: str, data_urls: list[str]) -> str:
    """Replace pending media slots (and then bare abstract slots) with embedded images."""
    if not html or not data_urls:
        return html or ""
    urls = [u for u in data_urls if str(u).startswith("data:")]
    if not urls:
        return html
    idx = 0

    def _wrap(tag: str, attrs: str, url: str, n: int) -> str:
        leo = _leo_id_from_attrs(attrs, f"media-{n}")
        clean_attrs = _strip_media_pending_attrs(attrs)
        # Keep leo-id on the wrapper; img is decorative fill
        if not _DATA_LEO_ID_RE.search(clean_attrs):
            clean_attrs = f'{clean_attrs} data-leo-id="{leo}"'
        return (
            f"<{tag}{clean_attrs}>"
            f'<img class="media-img" alt="" src="{url}"/>'
            f"</{tag}>"
        )

    def repl_pending(m: re.Match[str]) -> str:
        nonlocal idx
        if idx >= len(urls):
            return m.group(0)
        url = urls[idx]
        idx += 1
        return _wrap(m.group(1), m.group(2), url, idx)

    out = _PENDING_MEDIA_SLOT_RE.sub(repl_pending, html)

    def repl_abstract(m: re.Match[str]) -> str:
        nonlocal idx
        if idx >= len(urls):
            return m.group(0)
        inner = m.group(3) or ""
        if re.search(r"<img\b", inner, re.I):
            return m.group(0)
        url = urls[idx]
        idx += 1
        return _wrap(m.group(1), m.group(2), url, idx)

    if idx < len(urls):
        out = _ABSTRACT_SLOT_RE.sub(repl_abstract, out)
    return out


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
        "pitch": "bento",
        "saas": "bento",
        "startup": "bento",
        "cards": "bento",
        "ice": "bento",
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
    templates = bento_slide_templates_prompt()
    return f"""
## Web-canvas presentations (Leo) — TEMPLATE FILL MODE
When the user asks to create or edit a web presentation / deck inside the note:
1) FIRST emit the fenced ```leo-canvas JSON (complete, valid).
2) ONLY AFTER the fence, one short Russian sentence. Never claim success without a complete fence.
3) Keep html compact: 4–6 slides, aim &lt;40KB. Prefer fewer slides over truncated JSON.

CRITICAL formatting:
```leo-canvas
{{"action":"create","title":"Презентация","theme":"bento","html":"<!DOCTYPE html>..."}}
```

Edit existing (when open sheet_id / selections provided):
```leo-canvas
{{"action":"update","sheet_id":"123","patches":[{{"leo_id":"hero-title","outer_html":"..."}}]}}
```

NEVER paste JSON outside the fence. NEVER use ```json. NEVER show HTML/JSON in chat text.

JSON rules:
- theme: default **bento** for new decks (black / white / ice-blue pitch style). Other themes [{themes}] only if user asks
- action create|update as before; never spawn a second sheet for small edits
- html: full document with data-leo-theme="bento" on <html>

## Design system (bento) — DO NOT invent layouts
Fill ONLY the named templates below. Copy the HTML structure exactly; replace Russian/placeholder text and numbers from the note. Do not invent new grids, colors, fonts, or card systems.
Allowed classes: .deck .slide .slide-ink .slide-ice .slide-head .split-pane .pane-copy .pane-stage .stack .tile .tile-ink .tile-mute .tile-white .tile-num .metric-xl .pill .pill-outline .pill-ink .pill-ice .icon-circle .cards-3 .cards-4 .bento-shell .bento-2x2 .vbars .vbar .vbar-fill .vbar-val .vbar-label .year-rail .donut .legend .hbar-row .hbar-track .hbar-fill .title .display .body .lead .caption .spark .chart-svg .abstract .abstract-orb .abstract-grid .abstract-wave .abstract-arcs .media-slot .media-img
Colors come from the theme (ink / ice / white). No purple, no Inter, no landing CTAs on slide 1.
No «Узнать подробнее» / demo / buy buttons on the cover.
Mark filled nodes with unique data-leo-id. No <script>. No ``` inside html strings.

Visuals policy (important):
- Decorative / abstract / mood / cover art (no real photo) → use .abstract (+ .abstract-orb / .abstract-grid / .abstract-wave / .abstract-arcs). NEVER put charts there.
- Real photo / illustration / «сгенерируй картинку» / brand image → media slot with CSS fallback:
  <div class="media-slot abstract abstract-orb" data-leo-id="hero-art" data-leo-img="pending" data-leo-img-prompt="short English visual prompt" aria-hidden="true"></div>
  Do NOT invent <img src="http...">. Do NOT put base64 in html. Leo fills the slot after generation.
- Charts (.donut .vbars .spark .chart-svg .hbar-*) ONLY when the slide is explicitly about numbers from the note (revenue, share, growth). If unsure → abstract, not a chart.
- Do not invent fake scary charts with unclear axes.

Pick 4–6 templates that match the note (vary layouts; never 4× the same template):
{templates}

If the user only chats and does not ask for a presentation, do not emit leo-canvas.
""".strip()


def bento_slide_templates_prompt() -> str:
    """Compact HTML skeletons for the model to copy and fill."""
    return r"""
### T1 cover-split (first slide — no button; abstract art, not a chart)
<section class="slide slide-ink" data-leo-id="slide-cover"><div class="split-pane"><div class="pane-copy"><h1 class="title" data-leo-id="hero-title">Заголовок</h1><p class="pill pill-outline" data-leo-id="hero-pill">контекст</p><p class="body" data-leo-id="hero-body">Одна мысль.</p></div><div class="pane-stage" data-leo-id="hero-stage"><div class="media-slot abstract abstract-orb" data-leo-id="hero-art" aria-hidden="true"></div></div></div></section>

### T2 problem-3cards
<section class="slide" data-leo-id="slide-problem"><div class="slide-head"><h2 class="title" data-leo-id="problem-title">Проблема</h2><span class="icon-circle" aria-hidden="true">↓</span></div><div class="cards-3"><div class="tile" data-leo-id="p1"><p class="tile-num">01.</p><p class="body">…</p></div><div class="tile tile-ink" data-leo-id="p2"><p class="tile-num">02.</p><p class="body">…</p></div><div class="tile" data-leo-id="p3"><p class="tile-num">03.</p><p class="body">…</p></div></div></section>

### T3 split-donut
<section class="slide slide-ink" data-leo-id="slide-funds"><div class="split-pane"><div class="pane-copy"><h2 class="title" data-leo-id="funds-title">Куда идут деньги</h2><p class="body" data-leo-id="funds-body">…</p></div><div class="pane-stage"><div class="donut" data-leo-id="funds-donut"></div><div class="legend" data-leo-id="funds-legend"><span><i>A</i><b>40%</b></span><span><i>B</i><b>25%</b></span></div></div></div></section>

### T4 split-line + pill
<section class="slide slide-ink" data-leo-id="slide-opp"><div class="split-pane"><div class="pane-copy"><h2 class="title" data-leo-id="opp-title">Возможность</h2><p class="pill pill-ice" data-leo-id="opp-pill">ключ. цифра</p><p class="body" data-leo-id="opp-body">…</p></div><div class="pane-stage"><svg class="spark chart-svg" viewBox="0 0 220 120"><polyline fill="none" stroke="currentColor" stroke-width="4" points="0,100 40,85 80,90 120,55 160,60 220,20"/><circle cx="220" cy="20" r="5" fill="currentColor"/></svg></div></div></section>

### T5 highlights-bento
<section class="slide" data-leo-id="slide-hi"><div class="split-pane"><div class="pane-copy"><h2 class="title" data-leo-id="hi-title">Хайлайты</h2><p class="pill pill-ink" data-leo-id="hi-year">2026</p><p class="body" data-leo-id="hi-body">…</p></div><div class="bento-shell"><div class="bento-2x2"><div class="tile" data-leo-id="hi-a"><p class="metric-xl">12</p><p class="caption">метка</p></div><div class="tile tile-mute" data-leo-id="hi-b"><p class="metric-xl">87%</p><p class="caption">метка</p></div><div class="tile tile-mute" data-leo-id="hi-c"><p class="metric-xl">3×</p><p class="caption">метка</p></div><div class="tile" data-leo-id="hi-d"><p class="metric-xl">30k</p><p class="caption">метка</p></div></div></div></div></section>

### T6 revenue-bars (dark)
<section class="slide slide-ink" data-leo-id="slide-rev"><div class="slide-head"><h2 class="title" data-leo-id="rev-title">Выручка</h2><p class="pill pill-outline">по годам</p></div><div class="vbars" data-leo-id="rev-bars"><div class="vbar"><span class="vbar-val">1.2</span><span class="vbar-fill is-ink" style="height:35%"></span><span class="vbar-label">Y1</span></div><div class="vbar"><span class="vbar-val">2.1</span><span class="vbar-fill is-mid" style="height:55%"></span><span class="vbar-label">Y2</span></div><div class="vbar"><span class="vbar-val">3.4</span><span class="vbar-fill" style="height:75%"></span><span class="vbar-label">Y3</span></div><div class="vbar"><span class="vbar-val">4.8</span><span class="vbar-fill" style="height:95%"></span><span class="vbar-label">Y4</span></div></div></section>

### T7 metric-row (dark)
<section class="slide slide-ink" data-leo-id="slide-facts"><div class="slide-head"><h2 class="title" data-leo-id="facts-title">Факты</h2><p class="pill pill-outline">в цифрах</p></div><div class="cards-4"><div class="tile tile-white" data-leo-id="f1"><span class="icon-circle">→</span><p class="metric-xl">300%</p><p class="caption">рост</p></div><div class="tile tile-white" data-leo-id="f2"><span class="icon-circle">→</span><p class="metric-xl">30k</p><p class="caption">пользователи</p></div><div class="tile tile-white" data-leo-id="f3"><span class="icon-circle">→</span><p class="metric-xl">12</p><p class="caption">сделки</p></div><div class="tile tile-white" data-leo-id="f4"><span class="icon-circle">→</span><p class="metric-xl">2y</p><p class="caption">до окупаемости</p></div></div></section>

### T8 quote-ice
<section class="slide slide-ice" data-leo-id="slide-quote"><div class="tile tile-white" style="flex:1;justify-content:center;min-height:60vh"><p class="pill pill-ink">2026</p><p class="display" data-leo-id="quote-text" style="max-width:18ch">Короткая сильная фраза.</p></div></section>

### T9 abstract-stage (mood / CSS abstract — or pending real image)
<section class="slide" data-leo-id="slide-art"><div class="split-pane"><div class="pane-copy"><h2 class="title" data-leo-id="art-title">Визуал</h2><p class="body" data-leo-id="art-body">…</p></div><div class="pane-stage"><div class="media-slot abstract abstract-wave" data-leo-id="art-abstract" aria-hidden="true"></div></div></div></section>
""".strip()


def open_canvas_prompt_block(sheet_id: str | None, *, title: str | None = None) -> str:
    sid = str(sheet_id or "").strip()
    if not sid:
        return ""
    label = str(title or "").strip()
    extra = f' title="{label}"' if label else ""
    return (
        f"Open web-canvas sheet_id={sid}{extra}. "
        "For any edit/fix of this deck use action=update with this sheet_id. "
        "Prefer patches on data-leo-id nodes; change only what the user asked. "
        "Do NOT rewrite the whole deck for a small fix. "
        "Do NOT action=create unless the user explicitly asks for a new separate presentation."
    )


def selections_prompt_block(selections: list[dict[str, Any]] | None) -> str:
    rows = []
    sheet_ids: list[str] = []
    for i, sel in enumerate(selections or []):
        if not isinstance(sel, dict):
            continue
        leo_id = str(sel.get("leo_id") or sel.get("leoId") or "").strip()
        label = str(sel.get("label") or "").strip()
        snippet = str(sel.get("snippet") or "").strip()[:4000]
        sheet_id = str(sel.get("sheet_id") or sel.get("sheetId") or "").strip()
        if not leo_id and not snippet:
            continue
        if sheet_id and sheet_id not in sheet_ids:
            sheet_ids.append(sheet_id)
        rows.append(
            f"- [{i + 1}] sheet_id={sheet_id or '—'} leo_id={leo_id or '—'} "
            f"label={label or '—'}\\n  snippet:\\n{snippet}"
        )
    if not rows:
        return ""
    target = sheet_ids[0] if sheet_ids else "«sheet_id from selection»"
    return (
        "Selected web-canvas elements — SURGICAL PATCH MODE (strict):\n"
        f"- Respond with action=update, sheet_id={target}\n"
        "- Emit ONLY patches for the selected leo_id(s). Keep the same data-leo-id on each node\n"
        "- Do NOT include an html field. Do NOT rewrite other slides, theme, or layout\n"
        "- Do NOT action=create. Do NOT replace the whole deck\n"
        "- Change only the requested text/style inside the selected node(s)\n"
        "Selections:\n" + "\n".join(rows)
    )


def selection_sheet_ids(selections: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for sel in selections or []:
        if not isinstance(sel, dict):
            continue
        sid = str(sel.get("sheet_id") or sel.get("sheetId") or "").strip()
        if sid and sid not in out:
            out.append(sid)
    return out


def normalize_canvas_action_for_apply(
    action: dict[str, Any],
    *,
    selection_sheet_ids: list[str] | None = None,
    open_sheet_id: str | None = None,
) -> dict[str, Any]:
    """Force targeted edits onto an existing web sheet instead of spawning duplicates."""
    if not isinstance(action, dict):
        return action
    act = dict(action)
    sel_ids = [str(x).strip() for x in (selection_sheet_ids or []) if str(x).strip()]
    open_id = str(open_sheet_id or "").strip()
    current_id = str(act.get("sheet_id") or act.get("sheetId") or "").strip()
    verb = str(act.get("action") or "create").strip().lower()
    has_patches = isinstance(act.get("patches"), list) and bool(act.get("patches"))

    preferred = ""
    if sel_ids:
        preferred = sel_ids[0]
    elif current_id:
        preferred = current_id
    elif open_id and (verb == "update" or has_patches):
        preferred = open_id

    if sel_ids:
        # User pointed at nodes in an existing deck — never create a sibling sheet
        # and never allow a full-html rewrite that nukes the rest of the deck.
        act["action"] = "update"
        act["sheet_id"] = preferred or sel_ids[0]
        act.pop("sheetId", None)
        act.pop("html", None)
        return act

    if verb == "update":
        if not current_id and preferred:
            act["sheet_id"] = preferred
        elif not current_id and open_id:
            act["sheet_id"] = open_id
        act["action"] = "update"
        return act

    if has_patches and open_id:
        act["action"] = "update"
        act["sheet_id"] = open_id
        act.pop("sheetId", None)
        return act

    return act


def _is_canvas_action(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    act = str(data.get("action") or "").strip().lower()
    if act not in ("create", "update"):
        return False
    # Must look like a canvas payload, not arbitrary JSON.
    if data.get("html") or data.get("patches") or data.get("theme") or data.get("title"):
        return True
    return False


def _loads_canvas_json(raw: str) -> Optional[dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    # Strip accidental language label left inside fence
    if text.lower().startswith("leo-canvas"):
        text = text.split("\n", 1)[-1].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _salvage_truncated_canvas_json(text)
        if data is None:
            return None
    return data if _is_canvas_action(data) else None


def _salvage_truncated_canvas_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort close of truncated create payloads that already contain usable html."""
    raw = (text or "").strip()
    if not raw.startswith("{") or '"html"' not in raw:
        return None
    # Must look like a create/update attempt.
    if not _ACTION_KEY_RE.search(raw):
        return None
    # Close an open html string at the last complete tag, then close the object.
    candidate = raw
    if candidate.count('"') % 2 == 1:
        # Odd quotes → unclosed string; trim to last '>' or end of a tag-ish chunk
        cut = candidate.rfind(">")
        if cut < 0 or cut < candidate.find('"html"'):
            return None
        candidate = candidate[: cut + 1] + '"'
    # Drop trailing commas / unfinished keys
    candidate = re.sub(r",\s*$", "", candidate)
    # Close open braces/brackets
    opens = candidate.count("{") - candidate.count("}")
    bracks = candidate.count("[") - candidate.count("]")
    if opens < 0 or bracks < 0 or opens > 3:
        return None
    candidate += "]" * max(0, bracks) + "}" * max(0, opens)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    html = str(data.get("html") or "")
    if len(html) < 80 or "<" not in html:
        return None
    return data if _is_canvas_action(data) else None


def _extract_balanced_object(text: str, start: int) -> Optional[tuple[str, int]]:
    """Return (json_str, end_index) for a balanced {...} starting at start."""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], i + 1
    return None


def _object_start_before_action(text: str, action_match_start: int) -> int:
    """Find opening `{` of the object that contains the action key."""
    starts = [i for i, ch in enumerate(text[: action_match_start + 1]) if ch == "{"]
    for start in reversed(starts):
        extracted = _extract_balanced_object(text, start)
        if extracted and extracted[1] > action_match_start:
            return start
    return starts[-1] if starts else -1


_UNCLOSED_FENCE_RE = re.compile(
    r"```(?:leo-canvas|json|javascript)?\s*\n?(\{.*)\Z",
    re.IGNORECASE | re.DOTALL,
)


def canvas_payload_attempted(raw: str) -> bool:
    text = raw or ""
    return bool(
        re.search(r"```\s*leo-canvas", text, re.IGNORECASE)
        or _ACTION_KEY_RE.search(text)
    )


def looks_like_canvas_claim(text: str) -> bool:
    return bool(_CANVAS_CLAIM_RE.search(text or ""))


def reconcile_canvas_chat_answer(
    clean_answer: str,
    *,
    actions: list[dict[str, Any]] | None,
    applied: list[dict[str, Any]] | None,
    raw_answer: str = "",
) -> str:
    """Replace false «Готово» when no web sheet was actually created/updated."""
    clean = (clean_answer or "").strip()
    if applied:
        return clean or "Готово. Полотно обновлено."
    attempted = bool(actions) or canvas_payload_attempted(raw_answer)
    claimed = looks_like_canvas_claim(clean) or looks_like_canvas_claim(raw_answer)
    if attempted or claimed:
        return CANVAS_FAIL_MSG
    if not clean:
        return CANVAS_FAIL_MSG if attempted else clean
    return clean


def parse_leo_canvas_blocks(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip canvas payloads from answer; return (clean_answer, actions).

    Accepts ```leo-canvas / ```json fences and bare JSON with action create|update.
    Also handles unclosed fences / truncated responses by stripping the dump from chat.
    """
    actions: list[dict[str, Any]] = []
    if not text:
        return "", actions

    cleaned = text

    # Prefer balanced {...} after fence open — avoids early ``` inside HTML strings.
    guard = 0
    while guard < 5:
        guard += 1
        fm = _FENCE_OPEN_RE.search(cleaned)
        if not fm:
            break
        brace = cleaned.find("{", fm.end())
        if brace < 0:
            cleaned = cleaned[: fm.start()].rstrip()
            break
        extracted = _extract_balanced_object(cleaned, brace)
        if extracted:
            raw_obj, end = extracted
            data = _loads_canvas_json(raw_obj)
            # Consume through closing ``` if present
            rest = cleaned[end:]
            close = re.match(r"\s*```", rest)
            cut = end + (close.end() if close else 0)
            if data is not None:
                actions.append(data)
            cleaned = (cleaned[: fm.start()] + cleaned[cut:]).strip()
            continue
        # Truncated fence body
        data = _loads_canvas_json(cleaned[brace:])
        if data is not None:
            actions.append(data)
        cleaned = cleaned[: fm.start()].rstrip()
        break

    # Legacy non-greedy fence (if any remain with valid JSON)
    def _fence_repl(match: re.Match[str]) -> str:
        data = _loads_canvas_json(match.group(1) or "")
        if data is None:
            return ""
        actions.append(data)
        return ""

    cleaned = _LEO_CANVAS_RE.sub(_fence_repl, cleaned)

    # Unclosed fence (model hit token limit mid-JSON)
    um = _UNCLOSED_FENCE_RE.search(cleaned)
    if um:
        data = _loads_canvas_json(um.group(1) or "")
        if data is not None:
            actions.append(data)
        cleaned = cleaned[: um.start()].rstrip()

    # Bare JSON fallback (model forgot the fence)
    guard = 0
    while guard < 5:
        guard += 1
        m = _BARE_ACTION_RE.search(cleaned) or _ACTION_KEY_RE.search(cleaned)
        if not m:
            break
        if m.re is _ACTION_KEY_RE:
            start = _object_start_before_action(cleaned, m.start())
        else:
            start = cleaned.find("{", m.start())
        if start < 0:
            cleaned = cleaned[: m.start()].rstrip()
            break
        extracted = _extract_balanced_object(cleaned, start)
        if not extracted:
            data = _loads_canvas_json(cleaned[start:])
            if data is not None:
                actions.append(data)
            cleaned = cleaned[:start].rstrip()
            break
        raw_obj, end = extracted
        data = _loads_canvas_json(raw_obj)
        if data is None:
            cleaned = cleaned[: m.start()] + cleaned[m.start() + 1 :]
            continue
        actions.append(data)
        cleaned = (cleaned[:start] + cleaned[end:]).strip()

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    # Drop leftover fence markers if any
    cleaned = re.sub(r"```(?:leo-canvas|json)?\s*```", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if actions and not cleaned:
        cleaned = "Готово. Полотно обновлено."
    elif not actions and not cleaned:
        cleaned = CANVAS_FAIL_MSG
    return cleaned, actions


def _annotate_missing_ids(html: str) -> str:
    """Insert data-leo-id on key tags that lack one."""
    used = {m.group(1) for m in _DATA_LEO_ID_RE.finditer(html)}
    counter = [1]
    out: list[str] = []
    i = 0
    pattern = re.compile(
        r"<(section|article|header|footer|h[1-6]|p|button|a|figure|nav|div|table|svg|ol|ul|li|blockquote)(\s[^>]*)?>",
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
    selection_sheet_ids: list[str] | None = None,
    open_sheet_id: str | None = None,
) -> Optional[dict[str, Any]]:
    """Create or update a web sheet from a parsed leo-canvas action. Returns sheet dict."""
    action = normalize_canvas_action_for_apply(
        action,
        selection_sheet_ids=selection_sheet_ids,
        open_sheet_id=open_sheet_id,
    )
    act = str(action.get("action") or "create").strip().lower()
    title = str(action.get("title") or "").strip() or "Презентация"
    theme = normalize_theme(action.get("theme"))
    html_in = str(action.get("html") or "")
    patches = action.get("patches") if isinstance(action.get("patches"), list) else None
    sheet_id_raw = action.get("sheet_id") or action.get("sheetId")

    if act == "update":
        if sheet_id_raw is None or not str(sheet_id_raw).strip():
            return None
        try:
            sid = int(sheet_id_raw)
        except (TypeError, ValueError):
            return None
        existing = note_sheets_mod.get_extra_sheet(int(note["id"]), sid)
        if not existing:
            return None
        if normalize_kind(existing.get("kind")) != SHEET_KIND_WEB:
            return None
        existing_html = str(existing.get("body") or "")
        surgical = bool(selection_sheet_ids)
        # Targeted patches must run on the live sheet. Full html rewrite only when
        # there are no patches and this is NOT a selection-driven edit.
        if surgical:
            if not patches:
                return None
            base_html = existing_html if existing_html.strip() else html_in
        elif patches:
            base_html = existing_html if existing_html.strip() else html_in
        else:
            base_html = html_in if html_in.strip() else existing_html
        if not theme and base_html:
            theme = extract_theme_from_html(base_html)
        # Keep existing theme on surgical edits so a stray theme field cannot restyle the deck.
        if surgical:
            theme = extract_theme_from_html(existing_html) or theme
        finalized = finalize_canvas_html(base_html, theme=theme, patches=patches)
        return note_sheets_mod.update_sheet(
            int(note["id"]),
            sid,
            title=title if (action.get("title") and not surgical) else None,
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
