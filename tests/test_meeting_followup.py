from assistant.services import meeting_followup as mf


NOTE = {
    "id": 5, "title": "Синк по пилоту", "created_at": "2026-10-21T10:00:00+00:00",
    "meta": {
        "main_topic": "Пилот", "meeting_at": "2026-10-21T13:00:00+03:00",
        "decisions": ["Пилот в ноябре"],
        "open_questions": ["Кто подрядчик?"],
        "tasks": [
            {"task": "Прислать смету", "assignee": "Спикер 2", "assignee_name": "", "deadline": "до пятницы (24 окт)"},
            {"task": "Подготовить договор", "assignee": "Пётр Иванов", "assignee_name": "Пётр Иванов"},
            {"task": "Старое", "assignee": "Пётр Иванов", "completed": True},
            {"task": "Обновить план", "assignee": "SPEAKER_1"},
        ],
    },
}


def test_facts_drop_speaker_labels_and_done():
    f = mf._facts(NOTE)
    assert [(t["assignee"], t["task"]) for t in f["tasks"]] == [
        ("", "Прислать смету"), ("Пётр Иванов", "Подготовить договор"), ("", "Обновить план")]
    assert f["date"] == "2026-10-21"


def test_plain_followup_groups():
    text = mf.plain_followup(mf._facts(NOTE))
    assert "Решили:\n— Пилот в ноябре" in text
    assert "Пётр Иванов: Подготовить договор" in text
    assert "Нужно назначить ответственного:\n— Прислать смету — до пятницы (24 окт)\n— Обновить план" in text
    assert "Старое" not in text


def test_build_followup_falls_back_without_llm(monkeypatch):
    from assistant.nlu import llm
    from assistant.stores import notes as notes_store

    monkeypatch.setattr(notes_store, "get_accessible_note", lambda uid, nid, with_sharing=False: NOTE if nid == 5 else None)
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert mf.build_followup(1, 5).startswith("Коллеги, спасибо за встречу!")
    assert mf.build_followup(1, 6) is None
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: "Черновик")
    assert mf.build_followup(1, 5) == "Черновик"


def test_no_facts_no_followup():
    assert mf._facts({"meta": {"tasks": [], "decisions": []}}) is None


def test_summary_keyboard_has_followup(monkeypatch):
    from assistant.lib import telegram_notify
    from assistant.services import meeting_record_pipeline as mrp

    sent = {}
    monkeypatch.setattr(telegram_notify, "send_rich_message", lambda uid, body, **kw: sent.update(kw) or True)
    mrp.notify_summary_ready(user_id=1, topic="Синк", summary_text="## x", note_id=5)
    rows = sent["reply_markup"]["inline_keyboard"]
    assert rows[-1] == [{"text": "✉️ Фоллоу-ап участникам", "callback_data": "fu:5"}]
