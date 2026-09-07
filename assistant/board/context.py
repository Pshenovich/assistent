"""Структурированный контекст хода агента."""

from __future__ import annotations

from typing import Any

from assistant.board import store
from assistant.board.company_brief import format_company_context
from assistant.board.models import AGENT_META


def _fmt_company(ctx: dict[str, Any] | None, *, query: str = "") -> str:
    return format_company_context(ctx, query=query)


def load_company_pack(
    company_id: str | None,
    *,
    user_id: int | str | None = None,
    include_knowledge: bool = True,
) -> dict[str, Any] | None:
    pack = store.get_company_pack(str(company_id)) if company_id else None
    pack = attach_live_share(pack)
    if include_knowledge:
        pack = attach_knowledge_note(pack, user_id)
    return pack


def meeting_include_knowledge(meeting: dict[str, Any] | None) -> bool:
    extra = str((meeting or {}).get("extra_instruction") or "")
    return "[knowledge:off]" not in extra


def attach_knowledge_note(
    pack: dict[str, Any] | None, user_id: int | str | None
) -> dict[str, Any] | None:
    if user_id in (None, "", 0, "0"):
        return pack
    from assistant.board.share_source import share_body_to_text
    from assistant.stores import notes as notes_store

    try:
        notes = notes_store.list_knowledge_notes(user_id)
    except Exception:
        return pack
    kb_docs: list[dict[str, Any]] = []
    for note in notes:
        html = str(note.get("body") or "")
        text = share_body_to_text(html).strip()
        if not text:
            continue
        title = str(note.get("title") or "База знаний").strip() or "База знаний"
        kb_docs.append(
            {
                "filename": title,
                "kind": "knowledge",
                "text": text,
                "html": html,
                "updated_at": note.get("updated_at"),
            }
        )
    if not kb_docs:
        return pack
    out = dict(pack or {})
    docs = kb_docs + [
        d
        for d in (out.get("_documents") or [])
        if isinstance(d, dict) and d.get("kind") not in {"live", "knowledge"}
    ]
    out["_documents"] = docs
    first = kb_docs[0]
    single = len(kb_docs) == 1
    out["_live_share"] = {
        "title": first["filename"] if single else "",
        "text": first["text"]
        if single
        else "\n\n".join(f"{d['filename']}\n{d['text']}" for d in kb_docs),
        "html": first.get("html") if single else "",
        "updated_at": first.get("updated_at") if single else "",
    }
    out.pop("_live_share_error", None)
    return out


def attach_live_share(pack: dict[str, Any] | None) -> dict[str, Any] | None:
    from assistant.board.share_source import resolve_company_share

    stored = str((pack or {}).get("source_url") or "").strip()
    url, doc, err = resolve_company_share(stored)
    if not url and not pack:
        return pack
    out = dict(pack or {})
    if url:
        out["source_url"] = url
    docs = list(out.get("_documents") or [])
    if doc and (doc.get("text") or "").strip():
        live_doc = {
            "filename": str(doc.get("title") or "Документ компании"),
            "kind": "live",
            "text": str(doc.get("text") or ""),
        }
        docs = [live_doc] + [d for d in docs if d.get("kind") != "live"]
        out["_live_share"] = doc
        out.pop("_live_share_error", None)
    elif err:
        out["_live_share_error"] = err
    out["_documents"] = docs
    if (
        not (out.get("raw_text") or "").strip()
        and not docs
        and not out.get("source_url")
        and not any(out.get(k) for k in ("products", "team", "description"))
    ):
        return pack
    return out


def _fmt_user(ctx: dict[str, Any] | None) -> str:
    if not ctx:
        return "(не задан — используй /me)"
    raw = (ctx.get("raw_text") or "").strip()
    if raw:
        return raw[:2000]
    parts = []
    for key in ("role", "goals", "decision_style", "preferences", "priorities"):
        val = (ctx.get(key) or "").strip()
        if val:
            parts.append(f"{key}: {val}")
    return "\n".join(parts)[:2000] if parts else "(пусто)"


def _fmt_message(msg: dict[str, Any]) -> str:
    agent = str(msg.get("agent") or "?")
    title = AGENT_META.get(agent, {}).get("title", agent)
    body = (msg.get("content") or "").strip()
    mid = str(msg.get("id") or "")[:8]
    return f"[{agent} #{mid}] {title}\n{body}"


def build_agent_context(
    *,
    meeting_id: str,
    agent: str,
    mode: str = "DISCUSSION",
    task: str = "",
    past_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    meeting = store.get_meeting(meeting_id) or {}
    messages = store.list_messages(meeting_id)
    rounds = store.list_rounds(meeting_id)
    user_ctx = store.get_user_context(str(meeting.get("user_id") or ""))
    own = [m for m in messages if m.get("agent") == agent]
    others = [m for m in messages if m.get("agent") not in {agent, "CHAIR"}]
    last = messages[-1] if messages else None

    by_agent: dict[str, list[str]] = {"P": [], "A": [], "E": [], "I": [], "USER": []}
    for m in messages:
        a = str(m.get("agent") or "")
        if a in by_agent:
            by_agent[a].append((m.get("content") or "").strip())

    unresolved = []
    if rounds:
        last_round = rounds[-1]
        unresolved.append(str(last_round.get("open_questions") or ""))
        unresolved.append(str(last_round.get("disagreements") or ""))

    past_text = ""
    if past_decisions:
        lines = []
        for d in past_decisions[:5]:
            lines.append(
                f"- {d.get('created_at','')[:10]}: {d.get('problem','')} → {d.get('decision','')}"
            )
        past_text = "\n".join(lines)

    extra = (meeting.get("extra_instruction") or "").strip()
    extra = extra.replace("[knowledge:off]", "").strip()
    analysis = meeting.get("analysis") if isinstance(meeting.get("analysis"), dict) else {}
    objective = str(analysis.get("decision_required") or meeting.get("title") or "")
    query = str(meeting.get("original_question") or "")
    stored_brief = str(analysis.get("company_brief") or "").strip()
    company = load_company_pack(
        str(meeting["company_id"]) if meeting.get("company_id") else None,
        user_id=meeting.get("user_id"),
        include_knowledge=meeting_include_knowledge(meeting),
    )
    live_text = _fmt_company(company, query=query).strip()
    has_kb = any(
        isinstance(d, dict) and d.get("kind") == "knowledge"
        for d in ((company or {}).get("_documents") or [])
    )
    if has_kb:
        company_text = live_text
    elif stored_brief:
        company_text = stored_brief
    else:
        company_text = live_text

    default_task = (
        "Assume the current consensus may be wrong. Find the strongest reason not to implement it."
        if mode == "CHALLENGE"
        else "Respond to the strongest unresolved argument. Do not repeat your first position."
    )
    your_task = task or default_task
    if extra:
        your_task = f"{your_task}\n\nADDITIONAL INSTRUCTION:\n{extra}"
    your_task += (
        "\n\nПродукты, цены, команду и ограничения бери из COMPANY CONTEXT. "
        "Не выдумывай продукты, которых нет в каталоге."
    )

    text = f"""ORIGINAL QUESTION
{meeting.get("original_question") or ""}

COMPANY CONTEXT
{company_text}

USER CONTEXT
{_fmt_user(user_ctx)}

CURRENT OBJECTIVE
{objective}

DECISION TYPE / SEVERITY / REVERSIBILITY
{analysis.get("decision_type", "general")} / {analysis.get("severity", "MEDIUM")} / {analysis.get("reversibility", "PARTIALLY_REVERSIBLE")}

KNOWN FACTS
{chr(10).join(f"- {x}" for x in (analysis.get("known_facts") or [])[:8]) or "-"}

ASSUMPTIONS
{chr(10).join(f"- {x}" for x in (analysis.get("assumptions") or [])[:8]) or "-"}

RELEVANT PAST DECISIONS
{past_text or "(нет)"}

DISCUSSION SO FAR
P:
{chr(10).join(by_agent["P"]) or "(ещё не говорил)"}

A:
{chr(10).join(by_agent["A"]) or "(ещё не говорил)"}

E:
{chr(10).join(by_agent["E"]) or "(ещё не говорил)"}

I:
{chr(10).join(by_agent["I"]) or "(ещё не говорил)"}

USER:
{chr(10).join(by_agent["USER"]) or "(нет уточнений)"}

YOUR PREVIOUS POSITION
{(own[-1].get("content") if own else "(это первый ход)") or ""}

OTHER AGENTS' ARGUMENTS
{chr(10).join(_fmt_message(m) for m in others[-8:]) or "(пока только исходный вопрос)"}

LAST MESSAGE
{_fmt_message(last) if last else "(нет)"}

UNRESOLVED
{chr(10).join(u for u in unresolved if u.strip()) or "(ещё нет сводки раунда)"}

MODE
{mode}

YOUR TASK
{your_task}
"""
    return {
        "company_context": company,
        "user_context": user_ctx,
        "original_question": meeting.get("original_question"),
        "meeting_summary": rounds[-1].get("summary") if rounds else "",
        "recent_messages": messages[-12:],
        "unresolved_arguments": unresolved,
        "previous_position": own[-1].get("content") if own else "",
        "text": text.strip(),
        "mode": mode,
    }


def load_prompt(name: str) -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parent / "prompts" / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
