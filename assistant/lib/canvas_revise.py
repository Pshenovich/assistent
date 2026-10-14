"""Apply per-slide edit comments to a web-canvas deck with one model call.

Template decks get slot updates (only for the commented slides); older HTML
decks get section-level patches.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from assistant.lib import canvas_templates, web_canvas

MAX_COMMENTS = 20
MAX_COMMENT_LEN = 1000

_REVISE_SYSTEM_TEMPLATE = """
You edit slides of a Leo web-canvas template deck. The user left a comment under some slides.
Apply each comment ONLY to its slide. Keep everything the comment does not ask to change.
Content comes only from the note text; never invent numbers.

Answer with ONE fenced block and nothing else:
```leo-canvas
{{"action":"update","slides":[{{"id":"s3","slots":{{…full new slots…}}}}],"insert":[{{"after":"s3","role":"…","slots":{{…}}}}],"remove":["s5"]}}
```
- slides: slots for every commented slide you change; send changed fields (others are kept).
- You can change texts, items and numbers, and switch "variant" to another layout of the same role
  (see "Available layouts"). Colors, fonts and spacing are fixed by the design — if a comment asks for
  them, pick the closest layout or shorten/restructure the text instead of ignoring the comment.
- insert/remove only when a comment asks to split, add or delete a slide.
- Respect slot limits and item ranges from the schema.

Slot schema by role:
{schema}
""".strip()

_REVISE_SYSTEM_HTML = """
You edit slides of a Leo HTML web-canvas deck. The user left a comment under some slides.
Apply each comment ONLY to its slide and keep its classes, structure and data-leo-id values.
Content comes only from the note text; never invent numbers. No <script>.

Answer with ONE fenced block and nothing else:
```leo-canvas
{"action":"update","patches":[{"leo_id":"<slide data-leo-id>","outer_html":"<section …>…</section>"}]}
```
One patch per changed slide; outer_html is the full new <section> with the same data-leo-id.
""".strip()


def clean_comments(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[:MAX_COMMENTS]:
        sid = str(k or "").strip()[:64]
        text = re.sub(r"\s+", " ", str(v or "")).strip()[:MAX_COMMENT_LEN]
        if sid and text:
            out[sid] = text
    return out


def _section_html(html: str, leo_id: str) -> str:
    m = re.search(
        rf"""<section\b[^>]*\bdata-leo-id=["']{re.escape(leo_id)}["'][^>]*>.*?</section>""",
        html or "",
        re.IGNORECASE | re.DOTALL,
    )
    return m.group(0) if m else ""


def _first_action(answer: str) -> Optional[dict[str, Any]]:
    _, actions = web_canvas.parse_leo_canvas_blocks(answer or "")
    return actions[0] if actions else None


def revise(
    html: str,
    comments: dict[str, str],
    *,
    ask: Callable[[str, str], Optional[str]],
) -> tuple[Optional[str], dict[str, str]]:
    """Return (new_html or None, {slide_id: applied|unchanged|failed}).

    ``ask(system, user_message)`` calls the model and returns its raw answer.
    """
    results = {sid: "failed" for sid in comments}
    if not comments:
        return None, results
    if canvas_templates.is_template_html(html):
        return _revise_template(html, comments, ask, results)
    return _revise_html(html, comments, ask, results)


def _revise_template(
    html: str,
    comments: dict[str, str],
    ask: Callable[[str, str], Optional[str]],
    results: dict[str, str],
) -> tuple[Optional[str], dict[str, str]]:
    deck = canvas_templates.extract_deck(html)
    if deck is None:
        return None, results
    deck = canvas_templates.sync_text_edits(deck, html)
    ids = {str(s["id"]) for s in deck["slides"]}
    targets = {sid for sid in comments if sid in ids}
    for sid in comments:
        if sid not in ids:
            results[sid] = "failed"
    if not targets:
        return None, results
    system = _REVISE_SYSTEM_TEMPLATE.format(schema=canvas_templates.schema_prompt())
    user = (
        "Комментарии к слайдам:\n"
        + "\n".join(f"- {sid}: {comments[sid]}" for sid in comments if sid in targets)
        + "\n\nТекущие данные этих слайдов (JSON):\n"
        + canvas_templates.deck_prompt_json(deck, only=targets)
        + "\n\nДоступные макеты для этих слайдов:\n"
        + "\n".join(
            f"- {s['id']}: "
            + ", ".join(v.id for v in canvas_templates.variants_for_role(s["role"]))
            for s in deck["slides"]
            if str(s["id"]) in targets
        )
        + "\n\nВесь дек по порядку:\n"
        + canvas_templates.outline_prompt(deck)
    )
    answer = ask(system, user)
    action = _first_action(answer or "")
    if not action:
        return None, results
    # Only commented slides may change; inserts must follow a commented slide.
    upd = {
        "slides": [s for s in action.get("slides") or [] if isinstance(s, dict) and str(s.get("id")) in targets],
        "insert": [s for s in action.get("insert") or [] if isinstance(s, dict) and str(s.get("after")) in targets],
        "remove": [str(x) for x in action.get("remove") or [] if str(x) in targets],
    }
    before = {str(s["id"]): canvas_templates.deck_prompt_json(deck, only={str(s["id"])}) for s in deck["slides"]}
    canvas_templates.apply_slide_updates(deck, upd)
    after_ids = {str(s["id"]) for s in deck["slides"]}
    changed = False
    for sid in targets:
        if sid not in after_ids:
            results[sid] = "applied"
            changed = True
        elif canvas_templates.deck_prompt_json(deck, only={sid}) != before.get(sid):
            results[sid] = "applied"
            changed = True
        elif any(str(s.get("after")) == sid for s in upd["insert"]):
            results[sid] = "applied"
            changed = True
        else:
            results[sid] = "unchanged"
    if not changed:
        return None, results
    return canvas_templates.render_deck_html(deck, canvas_templates.extract_media(html)), results


def _revise_html(
    html: str,
    comments: dict[str, str],
    ask: Callable[[str, str], Optional[str]],
    results: dict[str, str],
) -> tuple[Optional[str], dict[str, str]]:
    sections = {sid: _section_html(html, sid) for sid in comments}
    targets = {sid for sid, sec in sections.items() if sec}
    if not targets:
        return None, results
    user = "\n\n".join(
        f"Слайд data-leo-id={sid}\nКомментарий: {comments[sid]}\nТекущий HTML:\n{sections[sid][:12000]}"
        for sid in comments
        if sid in targets
    )
    answer = ask(_REVISE_SYSTEM_HTML, user)
    action = _first_action(answer or "")
    patches = [
        p
        for p in (action or {}).get("patches") or []
        if isinstance(p, dict) and str(p.get("leo_id") or "") in targets and str(p.get("outer_html") or "").strip()
    ]
    if not patches:
        return None, results
    new_html = web_canvas.finalize_canvas_html(
        html, theme=web_canvas.extract_theme_from_html(html), patches=patches
    )
    for sid in targets:
        results[sid] = "applied" if any(str(p.get("leo_id")) == sid for p in patches) else "unchanged"
    return new_html, results
