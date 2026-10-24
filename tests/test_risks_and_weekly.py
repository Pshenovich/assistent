from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import focus_items as fi
from assistant.services import risk_rules

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    fi._CONN = None
    monkeypatch.setattr(fi, "_owner_names", lambda uid: ["Вова"])
    monkeypatch.setattr(fi, "sync_source", lambda *a, **k: None)
    yield
    notes_store._CONN = None
    fi._CONN = None


def meeting(nid, items, at):
    card = {"key": f"sumtasks:{nid}", "type": "summary_tasks", "source": "транскрипция",
            "note": {"id": nid, "kind": "transcription", "title": f"Встреча {nid}"}, "items": items}
    fi.ingest(1, [card], at)


def test_risk_cards(db):
    # буксует: одна задача на трёх встречах
    for i, nid in enumerate((1, 2, 3)):
        meeting(nid, [{"id": f"sumtask:{nid}:a", "text": "Оксана — Подготовить смету по пилоту"}], NOW - timedelta(days=10 - i))
    # просрочено за мной и за другими, без исполнителя со сроком
    meeting(4, [
        {"id": "sumtask:4:a", "text": "Вова — Согласовать бюджет — 2026-10-20", "mine": True},
        {"id": "sumtask:4:b", "text": "Пётр — Прислать договор — 2026-10-19"},
        {"id": "sumtask:4:c", "text": "Спикер 2 — Обновить сайт — 2026-10-23"},
        {"id": "sumtask:4:d", "text": "Спикер 2 — Когда-нибудь потом — 2026-12-01"},
        {"id": "sumtask:4:e", "text": "Анна — Разослать протокол — 2026-10-23"},
    ], NOW)
    cards = {c["tag"]: c for c in risk_rules.risk_cards(1, now=NOW)}
    assert set(cards) == {"⏰ ваши обещания", "⏰ ждёте от других", "🔁 буксует", "❓ нет ответственного"}
    assert cards["⏰ ваши обещания"]["title"] == "1 ваше обещание просрочено"
    assert cards["⏰ ждёте от других"]["source"] == "Пётр"
    assert "Подготовить смету" in cards["🔁 буксует"]["ask"]
    assert cards["❓ нет ответственного"]["lines"] == ["Спикер 2 — Обновить сайт (срок 2026-10-23)"]
    # у «Анны» исполнитель назван — это не «нет ответственного», даже если неясно, чьё обязательство
    assert all("Анна" not in x for x in cards["❓ нет ответственного"]["lines"])
    # «Уточнить» покажет пункты карточками: ключи пунктов реестра
    keys = cards["❓ нет ответственного"]["commitment_keys"]
    assert keys == ["sumtask:4:c"] and fi.get(1, keys[0])
    assert "items" not in cards["🔁 буксует"]
    assert cards["🔁 буксует"]["actions"][0]["act"] == "assistant"

    # закрыли — риск уходит
    fi.apply(1, "sumtask:4:a", "done")
    assert "⏰ ваши обещания" not in {c["tag"] for c in risk_rules.risk_cards(1, now=NOW)}


def test_weekly_commitment_facts(db):
    from assistant.services import proactive_reports as pr

    meeting(5, [{"id": "sumtask:5:a", "text": "Вова — Согласовать бюджет — 2026-10-20", "mine": True},
                {"id": "sumtask:5:b", "text": "Пётр — Прислать договор — 2026-10-19"},
                {"id": "sumtask:5:c", "text": "Пётр — Обновить план"}], NOW)
    fi.apply(1, "sumtask:5:c", "done")
    monday = datetime(2026, 10, 19, tzinfo=timezone.utc)
    f = pr._commitment_facts(1, monday, NOW)
    assert [r["text"] for r in f["closed_this_week"]] == ["Обновить план"]
    assert [r["text"] for r in f["overdue_mine"]] == ["Согласовать бюджет"]
    assert f["overdue_theirs"][0] == {"text": "Прислать договор", "who": "Пётр", "due": "2026-10-19"}


def test_meeting_hours():
    from assistant.services import proactive_reports as pr

    evs = [
        {"start": {"dateTime": "2026-10-20T10:00:00+03:00"}, "end": {"dateTime": "2026-10-20T11:30:00+03:00"}},
        {"start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}},
    ]
    assert pr._meeting_hours(evs) == 1.5
