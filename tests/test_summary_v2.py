import json

from assistant.lib import summary_normalize as sn
from assistant.lib.meeting_context import MeetingContext
from assistant.lib.summary_render import render_summary_html
from assistant.nlu import llm as llm_mod

TRANSCRIPT = (
    "Спикер 1:\nДобрый день. Саша, что скажешь по бюджету?\n\n"
    "Спикер 2:\nДумаю, уложимся. Я пришлю смету до пятницы.\n\n"
    "Спикер 1:\nХорошо. Петя, подготовь договор с подрядчиком.\n\n"
    "Спикер 3:\nСделаю. Решили: запускаем пилот в ноябре."
)

PEOPLE = [
    {"name": "Александр Смирнов", "email": "sasha@x.ru", "aliases": ["Саша"], "key": "sasha@x.ru"},
    {"name": "Пётр Иванов", "email": "petr@x.ru", "aliases": [], "key": "petr@x.ru"},
    {"name": "Вова Пшенович", "email": "", "aliases": ["Вова"], "key": "", "owner": True},
]


def test_locate_quote_exact_and_fuzzy():
    tn = sn._norm(TRANSCRIPT)
    assert sn.locate_quote("Я пришлю смету до пятницы.", tn) == 1.0
    assert sn.locate_quote("я пришлю эту смету до пятницы", tn) >= 0.8
    assert sn.locate_quote("обсудили маркетинговую стратегию на год", tn) < 0.6


def test_self_assignment_needs_confident_speaker():
    raw = sn.raw_task({"task": "Прислать смету", "assignee_basis": "self", "speaker": "Спикер 2",
                       "deadline": "до пятницы", "due_iso": "2026-10-23", "quote": "Я пришлю смету до пятницы"})
    low = {"Спикер 2": {"name": "Саша", "confidence": 0.5}}
    t = sn.resolve_tasks([raw], speaker_map=low, people=PEOPLE, meeting_at="2026-10-21T11:00:00+03:00", transcript=TRANSCRIPT)[0]
    assert t["assignee_name"] == ""
    assert t["assignee"] == "Спикер 2 (вероятно Саша)"
    assert t["owner_flag"] == "unknown"
    high = {"Спикер 2": {"name": "Саша", "confidence": 0.9}}
    t = sn.resolve_tasks([raw], speaker_map=high, people=PEOPLE, meeting_at="2026-10-21T11:00:00+03:00", transcript=TRANSCRIPT)[0]
    assert t["assignee"] == "Александр Смирнов"
    assert t["assignee_contact_key"] == "sasha@x.ru"
    assert t["owner_flag"] == "others"
    assert t["due_iso"] == "2026-10-23"
    assert t["deadline"] == "до пятницы (23 окт)"
    assert t["quote_score"] == 1.0


def test_named_assignee_ignores_speaker_label_and_matches_case_forms():
    raw = sn.raw_task({"task": "Подготовить договор", "assignee": "Пети", "assignee_basis": "named", "speaker": "Спикер 1"})
    t = sn.resolve_tasks([raw], speaker_map={}, people=PEOPLE, meeting_at="", transcript=TRANSCRIPT)[0]
    assert t["assignee"] == "Пётр Иванов"


def test_owner_flag_mine():
    raw = sn.raw_task({"task": "Согласовать пилот", "assignee": "Вова", "assignee_basis": "named"})
    t = sn.resolve_tasks([raw], speaker_map={}, people=PEOPLE, meeting_at="", transcript=TRANSCRIPT)[0]
    assert t["owner_flag"] == "mine"


def test_due_iso_out_of_range_dropped():
    assert sn.clean_due_iso("2020-01-01", sn.date(2026, 10, 21)) == ""
    assert sn.clean_due_iso("не дата", None) == ""
    assert sn.clean_due_iso("2026-11-01", sn.date(2026, 10, 21)) == "2026-11-01"


def test_merge_raw_tasks_dedupes_by_quote():
    a = sn.raw_task({"task": "Прислать смету", "speaker": "Спикер 2", "quote": "Я пришлю смету до пятницы"})
    b = sn.raw_task({"task": "Отправить смету по бюджету", "deadline": "до пятницы", "quote": "пришлю смету до пятницы"})
    c = sn.raw_task({"task": "Подготовить договор с подрядчиком", "assignee": "Петя"})
    out = sn.merge_raw_tasks([a], [b, c])
    assert len(out) == 2
    assert out[0]["deadline"] == "до пятницы"


def test_clean_speaker_map_drops_unknown_labels():
    m = sn.clean_speaker_map({"Спикер 2": {"name": "Саша", "confidence": 1.4}, "Спикер 9": {"name": "X", "confidence": 1}}, TRANSCRIPT)
    assert set(m) == {"Спикер 2"}
    assert m["Спикер 2"]["confidence"] == 1.0


def test_render_meeting_sections_and_tasks():
    data = {
        "content_type": "meeting",
        "brief": "Обсудили бюджет.\nДоговорились о пилоте.",
        "discussion": [{"topic": "Бюджет", "points": ["Уложимся"]}],
        "decisions": [{"text": "Запускаем пилот в ноябре"}],
        "tasks": [{"task": "Прислать смету", "assignee": "Саша", "deadline": "до пятницы (23 окт)"}],
        "open_questions": [], "risks": ["Сроки подрядчика"], "next_steps": [],
        "speaker_map": {"Спикер 2": {"name": "Саша", "confidence": 0.5}},
    }
    out = render_summary_html(data)
    assert out.startswith("<h2>📌 Кратко</h2><p>Обсудили бюджет.</p><p>Договорились о пилоте.</p>")
    assert "<h2>✅ Принятые решения</h2><ul><li>Запускаем пилот в ноябре</li></ul>" in out
    assert "<h2>📋 Задачи</h2><ul><li data-task>Саша — Прислать смету — до пятницы (23 окт)</li></ul>" in out
    assert "Открытые вопросы" not in out
    assert "Спикер 2 — Саша (предположительно)" in out


def test_render_interview_uses_sections():
    data = {"content_type": "interview", "brief": "Кандидат на бэкенд.",
            "sections": [{"title": "💪 Сильные стороны", "items": ["Python"]}], "tasks": []}
    out = render_summary_html(data)
    assert "<h2>📌 Краткое описание</h2>" in out
    assert "<h2>💪 Сильные стороны</h2><ul><li>Python</li></ul>" in out


def test_summarize_recording_v2_end_to_end(monkeypatch):
    main = {
        "content_type": "meeting", "confidence": 0.9, "main_topic": "Бюджет пилота",
        "brief": "Обсудили бюджет.",
        "decisions": [{"text": "Пилот в ноябре", "quote": "Решили: запускаем пилот в ноябре"}],
        "tasks": [{"task": "Прислать смету", "assignee_basis": "self", "speaker": "Спикер 2",
                   "deadline": "до пятницы", "due_iso": "2026-10-23", "quote": "Я пришлю смету до пятницы"}],
        "speaker_map": {"Спикер 2": {"name": "Саша", "confidence": 0.8, "evidence": "ответил на «Саша, что скажешь»"}},
    }
    extra = {"tasks": [{"task": "Подготовить договор с подрядчиком", "assignee": "Петя", "assignee_basis": "named",
                        "quote": "Петя, подготовь договор с подрядчиком"}]}
    seen = {}

    def fake_chat(system, user, **kw):
        seen[kw["operation"]] = json.loads(user)
        return json.dumps(main if kw["operation"] == "summarize_extract_llm" else extra, ensure_ascii=False)

    monkeypatch.setattr(llm_mod, "_chat", fake_chat)
    monkeypatch.setenv("SUMMARY_PIPELINE", "v2")
    ctx = MeetingContext(meeting_at="2026-10-21T11:00:00+03:00", timezone="Europe/Moscow", title="Бюджет",
                         owner="Вова Пшенович", people=PEOPLE)
    out = llm_mod.summarize_recording(TRANSCRIPT, speakers_detected=True, context=ctx)
    assert out["summary_version"] == 2
    assert seen["summarize_extract_llm"]["context"]["meeting_at"] == "2026-10-21T11:00:00+03:00"
    assert [t["assignee"] for t in out["tasks"]] == ["Александр Смирнов", "Пётр Иванов"]
    assert out["decisions"] == ["Пилот в ноябре"]
    assert "Александр Смирнов — Прислать смету — до пятницы (23 окт)" in out["summary"]
    meta = sn.v2_meta(out)
    assert "tasks" not in meta["summary_data"]
    assert sn.summary_data_from_meta({**meta, "tasks": out["tasks"]})["tasks"] == out["tasks"]


def test_summarize_recording_falls_back_to_v1(monkeypatch):
    calls = []

    def fake_chat(system, user, **kw):
        calls.append(kw["operation"])
        if kw["operation"] == "summarize_extract_llm":
            return "not json"
        if kw["operation"] == "summarize_llm":
            return json.dumps({"summary": "<h2>📌 Кратко</h2><p>x</p>", "tasks": []})
        return json.dumps({"tasks": []})

    monkeypatch.setattr(llm_mod, "_chat", fake_chat)
    out = llm_mod.summarize_recording(TRANSCRIPT)
    assert "summarize_llm" in calls
    assert out and "summary_version" not in out


def _v2_note(tmp_path, monkeypatch, *, edited: bool):
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store
    from assistant.lib import meeting_context

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    monkeypatch.setattr(tnotes, "_focus_summary_ready", lambda *a, **k: None)
    monkeypatch.setattr(
        meeting_context, "context_for_note",
        lambda uid, note: MeetingContext(meeting_at="2026-10-21T11:00:00+03:00", people=PEOPLE),
    )
    raw = sn.raw_task({"task": "Прислать смету", "assignee_basis": "self", "speaker": "Спикер 2",
                       "deadline": "до пятницы", "due_iso": "2026-10-23", "quote": "Я пришлю смету до пятницы"})
    smap = {"Спикер 2": {"name": "Саша", "confidence": 0.5, "evidence": "", "confirmed": False}}
    tasks = sn.resolve_tasks([raw], speaker_map=smap, people=PEOPLE,
                             meeting_at="2026-10-21T11:00:00+03:00", transcript=TRANSCRIPT)
    data = {"content_type": "meeting", "brief": "Обсудили бюджет.", "tasks": tasks, "speaker_map": smap}
    result = {"summary_version": 2, "summary_data": data, "speaker_map": smap, "tasks": tasks,
              "meeting_at": "2026-10-21T11:00:00+03:00", "decisions_detail": []}
    from assistant.lib.telegram_markdown import prepare_summary_markdown

    display = prepare_summary_markdown(render_summary_html(data), headline="Бюджет")
    meta = {**sn.v2_meta(result), "tasks": tasks, "summary_render": {"headline": "Бюджет"}}
    note = tnotes.create_with_summary(7, title="Бюджет", transcript=TRANSCRIPT, summary=display, meta=meta)
    assert "Спикер 2 (вероятно Саша) — Прислать смету" in note["body"]
    if edited:
        notes_store.update_note("7", int(note["id"]), body=note["body"] + "\n\nМоя правка", actor_user_id="7")
    return tnotes, notes_store, int(note["id"])


def test_confirm_speaker_rerenders_untouched_summary(tmp_path, monkeypatch):
    tnotes, notes_store, nid = _v2_note(tmp_path, monkeypatch, edited=False)
    out = tnotes.confirm_speaker(7, nid, "Спикер 2", "Саша")
    assert out["summary_rerendered"] is True
    assert "Александр Смирнов — Прислать смету — до пятницы (23 окт)" in out["body"]
    assert out["meta"]["tasks"][0]["assignee_contact_key"] == "sasha@x.ru"
    assert out["meta"]["speaker_map"]["Спикер 2"]["confirmed"] is True
    notes_store._CONN = None


def test_confirm_speaker_keeps_edited_body(tmp_path, monkeypatch):
    tnotes, notes_store, nid = _v2_note(tmp_path, monkeypatch, edited=True)
    out = tnotes.confirm_speaker(7, nid, "Спикер 2", "Саша")
    assert out["summary_rerendered"] is False
    assert "Моя правка" in out["body"]
    assert out["meta"]["tasks"][0]["assignee"] == "Александр Смирнов"
    notes_store._CONN = None


def test_raw_task_cleans_placeholder_values_and_speaker_assignee():
    t = sn.raw_task({"task": "x", "assignee": "none", "deadline": "null"})
    assert t["assignee"] == "" and t["deadline"] == "" and t["assignee_basis"] == "none"
    t = sn.raw_task({"task": "x", "assignee": "SPEAKER_3"})
    assert t["assignee"] == "" and t["assignee_basis"] == "self" and t["speaker"] == "Спикер 3"


def test_tasks_duplicate_paraphrases():
    d = lambda a, b, **kw: sn.tasks_duplicate(sn.raw_task({"task": a, **kw}), sn.raw_task({"task": b}))
    assert d("Поделиться файлом с правилами кодирования после завершения работы.",
             "Поделиться файликом после завершения работы над ним.")
    assert not d("Сделать мердж-реквест по редизайну", "Отдать мердж-реквест на ревью")
    assert not d("Обсудить с Артёмом проблему с запросом номера договора на лайках.",
                 "Обсудить с Настей, что не так на лайках и как можно это улучшить.")
    a = sn.raw_task({"task": "Подготовить отчёт по продажам", "assignee": "Оксана"})
    b = sn.raw_task({"task": "Подготовить отчёт по продажам за месяц", "assignee": "Рафис"})
    assert not sn.tasks_duplicate(a, b)
