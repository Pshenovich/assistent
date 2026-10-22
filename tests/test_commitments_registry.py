import json
from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import focus_items as fi

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    fi._CONN = None
    monkeypatch.setattr(fi, "_owner_names", lambda uid: ["Вова Пшенович", "Вова"])
    monkeypatch.setattr(fi, "sync_source", lambda *a, **k: None)
    yield
    notes_store._CONN = None
    fi._CONN = None


def summary_card(nid, items):
    return {"key": f"sumtasks:{nid}", "type": "summary_tasks", "source": "транскрипция · вчера",
            "note": {"id": nid, "kind": "transcription", "title": f"Встреча {nid}"}, "items": items}


def chat_card(key, text, kind="chat_promise"):
    return {"key": key, "type": kind, "source": "чат «Пилот» · сегодня",
            "chat": {"id": -100, "title": "Пилот"}, "_item": {"text": text, "who": "", "due": ""}}


def agreement_card(key, nid, text, who, due=""):
    return {"key": key, "type": "agreement", "source": "заметка",
            "note": {"id": nid, "kind": "note", "title": "Заметка"}, "_item": {"text": text, "who": who, "due": due, "quote": "q"}}


def test_direction_rules():
    names = ["Вова Пшенович"]
    assert fi.direction_for({"kind": "chat_request", "who": "Оксана"}, names) == "mine"
    assert fi.direction_for({"kind": "summary_task", "who": "Спикер 2"}, names) == "unknown"
    assert fi.direction_for({"kind": "summary_task", "who": "", "mine": True}, names) == "mine"
    assert fi.direction_for({"kind": "agreement", "who": "я"}, names) == "mine"
    assert fi.direction_for({"kind": "agreement", "who": "Вове"}, names) == "mine"
    assert fi.direction_for({"kind": "agreement", "who": "Оксана"}, names) == "theirs"


def test_ingest_dedup_across_sources_and_feed(db):
    meeting = summary_card(10, [
        {"id": "sumtask:10:a", "text": "Оксана — Подготовить смету по пилоту — 2026-10-20", "quote": "Оксана, подготовь смету"},
        {"id": "sumtask:10:b", "text": "Вова — Согласовать бюджет", "mine": True},
    ])
    note = agreement_card("ins:20:x", 20, "Подготовить смету для пилота", "Оксана")
    assert fi.ingest(1, [meeting], NOW) == 2
    assert fi.ingest(1, [note], NOW + timedelta(minutes=1)) == 1
    assert fi.ingest(1, [meeting, note], NOW) == 0  # повторная запись не плодит строк

    a, b, n = fi.get(1, "sumtask:10:a"), fi.get(1, "sumtask:10:b"), fi.get(1, "ins:20:x")
    assert a["commitment_id"] == n["commitment_id"] != b["commitment_id"]
    assert (a["direction"], b["direction"]) == ("theirs", "mine")
    assert a["quote"] == "Оксана, подготовь смету" and a["due_iso"] == "2026-10-20"

    feed = fi.apply_to_feed(1, [meeting, note], NOW)
    assert [c["key"] for c in feed] == ["sumtasks:10"]  # заметка — дубль встречи

    open_ = fi.commitments(1, now=NOW)
    assert len(open_) == 2
    smeta = next(c for c in open_ if "смет" in c["text"])
    assert smeta["status"] == "overdue" and len(smeta["sources"]) == 2

    # закрыли в заметке — исчезает и из встречи
    fi.apply(1, "ins:20:x", "done")
    feed = fi.apply_to_feed(1, [meeting, note], NOW)
    assert [i["id"] for i in feed[0]["items"]] == ["sumtask:10:b"]
    assert [c["text"] for c in fi.commitments(1, now=NOW)] == ["Согласовать бюджет"]
    assert fi.commitments(1, status="closed", now=NOW)[0]["closed_as"] == "completed"
    assert [c["direction"] for c in fi.commitments(1, direction="mine", now=NOW)] == ["mine"]


def test_same_source_not_merged(db):
    card = summary_card(11, [{"id": "sumtask:11:a", "text": "Оксана — Подготовить смету"},
                             {"id": "sumtask:11:b", "text": "Оксана — Подготовить смету по пилоту"}])
    fi.ingest(1, [card], NOW)
    assert fi.get(1, "sumtask:11:a")["commitment_id"] != fi.get(1, "sumtask:11:b")["commitment_id"]


def test_manual_row_gets_registry_fields(db):
    card = chat_card("chat:-100:2026-10-22:abc", "Прислать отчёт до пятницы")
    fi.remember(1, fi.item_from_card(card)[0])
    fi.apply(1, "chat:-100:2026-10-22:abc", "snooze", until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    fi.ingest(1, [card], NOW)
    r = fi.get(1, "chat:-100:2026-10-22:abc")
    assert r["commitment_id"] and r["direction"] == "mine" and r["status"] == "snoozed"


def test_tool_commitments(db):
    from assistant.services.gpt_calendar_tools import GptCalendarContext, tool_commitments

    fi.ingest(1, [summary_card(12, [{"id": "sumtask:12:a", "text": "Оксана — Подготовить смету"},
                                    {"id": "sumtask:12:b", "text": "Пётр — Обновить план"}])], NOW)
    res = json.loads(tool_commitments(GptCalendarContext(uid=1), {"direction": "theirs", "who": "Оксане"}))
    assert res["total"] == 1 and res["commitments"][0]["text"] == "Подготовить смету"
    assert res["commitments"][0]["direction"] == "за другими"
