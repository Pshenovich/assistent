"""Миграция journal transcribe/summarize → transcription notes."""

from __future__ import annotations

from typing import Any

from assistant.lib.usage_store import (
    find_summary_event_for_transcript,
    journal_json_from_raw,
    journal_meta_from_raw,
    journal_text_from_raw,
    list_journal_events_for_ops,
    list_user_ids_with_journal_ops,
    mark_journal_event_migrated,
)
from assistant.services import transcription_notes as tnotes
from assistant.stores import notes as notes_store

_OPS = ("obuchat_transcribe", "summarize")


def _title_for(row: dict[str, Any], raw: str | None, fallback: str) -> str:
    meta = journal_meta_from_raw(raw)
    j = journal_json_from_raw(raw)
    topic = (
        str(meta.get("main_topic") or "").strip()
        or str(meta.get("meeting_topic") or "").strip()
        or str(j.get("filename") or "").strip()
    )
    if topic:
        return topic[:200]
    return fallback


def _meta_from_event(row: dict[str, Any], raw: str | None) -> dict[str, Any]:
    j = journal_json_from_raw(raw)
    meta = dict(journal_meta_from_raw(raw))
    source_url = str(j.get("source_url") or j.get("url") or "").strip()
    if source_url:
        meta["source_url"] = source_url
    tg = str(j.get("telegram_link") or "").strip()
    if tg:
        meta["telegram_link"] = tg
    filename = str(j.get("filename") or "").strip()
    if filename:
        meta["filename"] = filename
    source = str(j.get("source") or "").strip()
    if source:
        meta["source"] = source
    return meta


def migrate_user(user_id: int | str) -> dict[str, int]:
    uid = str(int(user_id))
    events = list_journal_events_for_ops(uid, _OPS, include_migrated=False)
    summaries_by_id = {
        int(r["id"]): r
        for r in events
        if str(r.get("operation") or "") == "summarize"
    }
    transcripts = [
        r for r in events if str(r.get("operation") or "") == "obuchat_transcribe"
    ]
    paired_summary_ids: set[int] = set()
    created = 0
    skipped = 0

    for tr in transcripts:
        tr_id = int(tr["id"])
        tr_raw = str(tr.get("raw_usage_json") or "") if tr.get("raw_usage_json") else None
        transcript_text = journal_text_from_raw(tr_raw)
        linked = find_summary_event_for_transcript(uid, tr_id)
        sm = summaries_by_id.get(int(linked)) if linked else None
        if sm:
            paired_summary_ids.add(int(sm["id"]))
            sm_raw = (
                str(sm.get("raw_usage_json") or "") if sm.get("raw_usage_json") else None
            )
            summary_text = journal_text_from_raw(sm_raw)
            sm_j = journal_json_from_raw(sm_raw)
            if not transcript_text:
                transcript_text = str(sm_j.get("transcript") or "").strip()
            meta = _meta_from_event(sm, sm_raw)
            meta.update({k: v for k, v in _meta_from_event(tr, tr_raw).items() if v})
            title = _title_for(sm, sm_raw, _title_for(tr, tr_raw, "Саммари"))
            if summary_text:
                note = tnotes.create_with_summary(
                    uid,
                    title=title,
                    transcript=transcript_text or summary_text,
                    summary=summary_text,
                    meta=meta,
                )
            else:
                note = tnotes.create_transcript_only(
                    uid,
                    title=_title_for(tr, tr_raw, "Транскрипция"),
                    transcript=transcript_text,
                    meta=meta,
                )
            nid = int(note["id"])
            mark_journal_event_migrated(uid, tr_id, nid)
            mark_journal_event_migrated(uid, int(sm["id"]), nid)
            created += 1
        else:
            if not transcript_text:
                skipped += 1
                continue
            note = tnotes.create_transcript_only(
                uid,
                title=_title_for(tr, tr_raw, "Транскрипция"),
                transcript=transcript_text,
                meta=_meta_from_event(tr, tr_raw),
            )
            mark_journal_event_migrated(uid, tr_id, int(note["id"]))
            created += 1

    for sm_id, sm in summaries_by_id.items():
        if sm_id in paired_summary_ids:
            continue
        sm_raw = str(sm.get("raw_usage_json") or "") if sm.get("raw_usage_json") else None
        summary_text = journal_text_from_raw(sm_raw)
        sm_j = journal_json_from_raw(sm_raw)
        transcript_text = str(sm_j.get("transcript") or "").strip()
        if not summary_text and not transcript_text:
            skipped += 1
            continue
        meta = _meta_from_event(sm, sm_raw)
        title = _title_for(sm, sm_raw, "Саммари")
        if summary_text and transcript_text:
            note = tnotes.create_with_summary(
                uid,
                title=title,
                transcript=transcript_text,
                summary=summary_text,
                meta=meta,
            )
        elif summary_text:
            note = notes_store.create_note(
                uid,
                title,
                tnotes._as_html(summary_text),
                role=notes_store.TRANSCRIPTION_ROLE,
                meta=meta,
                primary_sheet_title=notes_store.SUMMARY_SHEET_TITLE,
            )
        else:
            note = tnotes.create_transcript_only(
                uid, title=title, transcript=transcript_text, meta=meta
            )
        mark_journal_event_migrated(uid, sm_id, int(note["id"]))
        created += 1

    return {"created": created, "skipped": skipped}


def migrate_all() -> dict[str, Any]:
    users = list_user_ids_with_journal_ops(_OPS)
    totals = {"users": 0, "created": 0, "skipped": 0}
    for uid in users:
        try:
            stats = migrate_user(uid)
        except Exception as e:
            print(f"[transcription_migrate] user={uid} err={e!r}")
            continue
        totals["users"] += 1
        totals["created"] += int(stats.get("created") or 0)
        totals["skipped"] += int(stats.get("skipped") or 0)
    return totals


def migrate_user_if_needed(user_id: int | str) -> dict[str, int]:
    uid = str(int(user_id))
    pending = list_journal_events_for_ops(uid, _OPS, include_migrated=False, limit=1)
    if not pending:
        return {"created": 0, "skipped": 0}
    return migrate_user(uid)
