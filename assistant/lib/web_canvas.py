"""Web-canvas полотна: kit, post-process HTML, парсинг leo-canvas блоков из ответа GPT."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from assistant.lib import canvas_templates

SHEET_KIND_DOC = "doc"
SHEET_KIND_WEB = "web"
SHEET_KIND_BOARD = "board"
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
    "Не удалось собрать презентацию — ответ модели оборвался или не распознан. "
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


MAX_IMAGE_B64 = 1_800_000


def shrink_image_b64(b64: str, mime: str) -> tuple[str, str]:
    """Слишком большую картинку (PNG 1024² от модели бывает > 1,8 МБ) пережать в JPEG ≤1600px.

    Без Pillow или при ошибке возвращаем как есть — вызывающий сам отбросит большую."""
    if len(b64) <= MAX_IMAGE_B64:
        return b64, mime
    try:
        import base64
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        im = im.convert("RGB")
        im.thumbnail((1600, 1600))
        for quality in (86, 75, 62):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            out = base64.b64encode(buf.getvalue()).decode("ascii")
            if len(out) <= MAX_IMAGE_B64:
                return out, "image/jpeg"
    except Exception as e:  # noqa: BLE001
        print(f"[web_canvas] image shrink failed err={e!r}")
    return b64, mime


def image_dicts_to_data_urls(images: list[dict[str, str]] | None) -> list[str]:
    """Convert openrouter image dicts ({mime,b64}) to data URLs."""
    out: list[str] = []
    for img in images or []:
        if not isinstance(img, dict):
            continue
        b64 = str(img.get("b64") or "").strip()
        mime = str(img.get("mime") or "image/png").split(";")[0].strip() or "image/png"
        if b64:
            b64, mime = shrink_image_b64(b64, mime)
        if not b64 or len(b64) > MAX_IMAGE_B64:
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
    if k in (SHEET_KIND_BOARD, "whiteboard"):
        return SHEET_KIND_BOARD
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
    schema = canvas_templates.schema_prompt()
    return f"""
## Web-canvas presentations (Leo) — SLOT TEMPLATES
When the user asks to create a presentation / deck in the note:
1) FIRST emit ONE fenced ```leo-canvas JSON. 2) AFTER the fence — one short Russian sentence.
Never write HTML. You only choose slide roles and fill slot data; Leo renders the layouts.

Create (6–10 slides):
```leo-canvas
{{"action":"create","title":"Название","slides":[{{"role":"cover","slots":{{"title":"…","lede":"…","goals":["…","…","…"]}}}},{{"role":"chart","slots":{{"title":"…","chart":{{"intent":"trend","x":["Апр","Май"],"series":[{{"values":[1,2]}}]}},"source":"…"}}}}]}}
```
- role is required. variant is optional: Leo picks the layout from the data shape.
- Arc (skip what the note has no material for): cover → context → insight → metrics/chart → comparison/content → plan → closing → appendix.
- Content ONLY from the note and attachments. Never invent numbers: if there are no numbers, use text roles (insight, content), not metrics/chart.
- Use role=chart only for real numeric series from the source; pick chart.intent by what the reader must see.
- Keep texts within the limits (≤N chars). Russian text. No markdown inside slots.
- Repeated items: respect the ranges (e.g. 2–4). If there are more points, split into two slides.
- Style: add "theme" ONLY if the user names one — "analytic" (Аналитика: тёмная, зелёные акценты) or
  "mono" (Монохром: чёрный, белый и голубой). Otherwise omit it: Leo uses the user's last style.
  To restyle an open deck without changing content: {{"action":"update","sheet_id":"123","theme":"mono"}}.

Edit an existing template deck (an open sheet with slide ids s1, s2, …):
```leo-canvas
{{"action":"update","sheet_id":"123","slides":[{{"id":"s3","slots":{{…full new slots of s3…}}}}],"insert":[{{"after":"s3","role":"content","slots":{{…}}}}],"remove":["s7"]}}
```
- Send FULL slots for every slide you change; omit slides you do not change.
- To change only the layout of a slide send {{"id":"s3","variant":"<variant id>"}} with its current slots.

Older decks without slide ids are HTML decks: edit them with
{{"action":"update","sheet_id":"123","patches":[{{"leo_id":"…","outer_html":"…"}}]}}

Slot schema by role (variant id → slots):
{schema}

NEVER paste JSON outside the fence. NEVER use ```json. If the user only chats, do not emit leo-canvas.
""".strip()


def open_canvas_prompt_block(
    sheet_id: str | None,
    *,
    title: str | None = None,
    html: str | None = None,
) -> str:
    sid = str(sheet_id or "").strip()
    if not sid:
        return ""
    label = str(title or "").strip()
    extra = f' title="{label}"' if label else ""
    deck = canvas_templates.extract_deck(html or "") if html else None
    if deck is not None:
        return (
            f"Open template deck sheet_id={sid}{extra}. "
            "For any edit use action=update with this sheet_id and slides:[{id, slots}] "
            "(full slots of each changed slide). Change only what the user asked. "
            "Do NOT action=create unless the user explicitly asks for a new separate presentation.\n"
            "Current deck JSON:\n" + canvas_templates.deck_prompt_json(deck)[:24000]
        )
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
        "- Template deck (leo_id like s3 or s3-title): instead of patches send "
        "slides:[{id:\"s3\", slots:{...full slots...}}] for that slide only\n"
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
    if (
        data.get("html")
        or data.get("patches")
        or data.get("slides")
        or data.get("insert")
        or data.get("remove")
        or data.get("theme")
        or data.get("title")
    ):
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
    if canvas_templates.is_template_html(html or ""):
        rendered = canvas_templates.refinalize(html or "")
        if rendered is not None:
            return rendered
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
    default_deck_theme: str | None = None,
) -> Optional[dict[str, Any]]:
    """Create or update a web sheet from a parsed leo-canvas action. Returns sheet dict.

    default_deck_theme — стиль новой презентации, если модель его не указала
    (последний выбранный пользователем).
    """
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
        if canvas_templates.is_template_html(existing_html):
            return _update_template_sheet(
                note, sid, existing_html, action, note_sheets_mod, surgical=surgical
            )
        if canvas_templates.is_template_action(action) and not surgical:
            # The model rebuilt an old HTML deck as a template deck.
            rebuilt = {**action, "title": action.get("title") or existing.get("title")}
            if str(rebuilt.get("theme") or "") not in canvas_templates.THEMES and default_deck_theme:
                rebuilt["theme"] = default_deck_theme
            deck = canvas_templates.build_deck(rebuilt)
            if not deck["slides"]:
                return None
            return note_sheets_mod.update_sheet(
                int(note["id"]),
                sid,
                title=title if action.get("title") else None,
                body=canvas_templates.render_deck_html(deck, canvas_templates.with_default_decor(deck)),
                kind=SHEET_KIND_WEB,
            )
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

    if canvas_templates.is_template_action(action):
        if str(action.get("theme") or "") not in canvas_templates.THEMES and default_deck_theme:
            action = dict(action, theme=default_deck_theme)
        deck = canvas_templates.build_deck(action)
        if not deck["slides"]:
            return None
        return note_sheets_mod.create_sheet(
            note,
            title=title,
            body=canvas_templates.render_deck_html(deck, canvas_templates.with_default_decor(deck)),
            kind=SHEET_KIND_WEB,
        )

    finalized = finalize_canvas_html(html_in, theme=theme, patches=patches)
    return note_sheets_mod.create_sheet(
        note,
        title=title,
        body=finalized,
        kind=SHEET_KIND_WEB,
    )


_TAG_RE = re.compile(r"<[^>]+>")


def _patches_to_slots(deck: dict[str, Any], patches: list[Any]) -> list[str]:
    """Map legacy text patches on template nodes (leo_id «s3-title») onto slots."""
    import html as html_lib

    touched: list[str] = []
    ids = {str(s["id"]) for s in deck.get("slides") or []}
    for p in patches or []:
        if not isinstance(p, dict):
            continue
        leo_id = str(p.get("leo_id") or p.get("leoId") or "").strip()
        sid, _, path = leo_id.partition("-")
        if sid not in ids or not path:
            continue
        text = html_lib.unescape(_TAG_RE.sub(" ", str(p.get("outer_html") or p.get("outerHtml") or "")))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        slide = next(s for s in deck["slides"] if str(s["id"]) == sid)
        if canvas_templates.get_path(slide["slots"], path) is None:
            continue
        canvas_templates.set_path(slide["slots"], path, text)
        touched.append(sid)
    return touched


def _update_template_sheet(
    note: dict[str, Any],
    sid: int,
    existing_html: str,
    action: dict[str, Any],
    note_sheets_mod: Any,
    *,
    surgical: bool,
) -> Optional[dict[str, Any]]:
    deck = canvas_templates.extract_deck(existing_html)
    if deck is None:
        return None
    ids_before = {str(x.get("id")) for x in deck.get("slides") or []}
    touched: list[str] = []
    # «Сделай в монохроме» — меняем только стиль, содержимое остаётся.
    want_theme = str(action.get("theme") or "")
    if want_theme in canvas_templates.THEMES and want_theme != canvas_templates.deck_theme(deck):
        deck["theme"] = want_theme
        touched.append("theme")
    if any(action.get(k) for k in ("slides", "insert", "remove")):
        if surgical:
            # Selection edits may only change the slides they name.
            action = {"slides": action.get("slides") or []}
        touched += canvas_templates.apply_slide_updates(deck, action)
    patches = action.get("patches") if isinstance(action.get("patches"), list) else []
    if patches:
        touched += _patches_to_slots(deck, patches)
    new_title = str(action.get("title") or "").strip()
    if new_title and not surgical:
        deck["title"] = new_title[:120]
    if not touched and not (new_title and not surgical):
        return None
    new_ids = [str(x.get("id")) for x in deck.get("slides") or [] if str(x.get("id")) not in ids_before]
    media = canvas_templates.with_default_decor(deck, canvas_templates.extract_media(existing_html), only=new_ids)
    html = canvas_templates.render_deck_html(deck, media)
    return note_sheets_mod.update_sheet(
        int(note["id"]),
        sid,
        title=new_title if (new_title and not surgical) else None,
        body=html,
        kind=SHEET_KIND_WEB,
    )
