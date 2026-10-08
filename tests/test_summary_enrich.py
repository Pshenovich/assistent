import pytest

from assistant.lib.summary_enrich import inject_tasks_into_summary, merge_tasks
from assistant.lib.telegram_rich import tasks_checklist_html


def test_merge_tasks_keeps_all_unique_items():
    primary = [{"assignee": "Иван", "task": "Подготовить отчёт", "deadline": ""}]
    secondary = [
        {"assignee": "", "task": "Согласовать бюджет", "deadline": "пятница"},
        {"assignee": "Мария", "task": "Отправить договор", "deadline": ""},
        {"assignee": "", "task": "подготовить отчёт", "deadline": ""},
    ]
    merged = merge_tasks(primary, secondary)
    assert len(merged) == 3
    assert merged[0]["task"] == "Подготовить отчёт"
    assert merged[0]["assignee"] == "Иван"


def test_inject_tasks_replaces_tasks_section_and_keeps_next_sections():
    summary = (
        "<h2>📌 Кратко</h2><p>Обсудили релиз.</p>\n"
        "<h2>📋 Задачи</h2><ul><li>Иван — сделать одно</li></ul>\n"
        "<h2>🔜 Следующие шаги</h2><p>Встретиться снова.</p>"
    )
    tasks = [
        {"assignee": "Иван", "task": "Подготовить отчёт", "deadline": ""},
        {"assignee": "Мария", "task": "Согласовать бюджет", "deadline": "пятница"},
        {"assignee": "", "task": "Отправить договор", "deadline": ""},
        {"assignee": "Пётр", "task": "Обновить roadmap", "deadline": "", "completed": True},
    ]
    out = inject_tasks_into_summary(summary, tasks)
    assert out.count("<li>") == 3
    assert out.count("<li checked>") == 1
    assert "Мария — Согласовать бюджет — пятница" in out
    assert "<h2>✅ Выполненные задачи</h2>" in out
    assert "сделать одно" not in out
    assert out.startswith("<h2>📌 Кратко</h2><p>Обсудили релиз.</p>")
    assert out.endswith("<h2>🔜 Следующие шаги</h2><p>Встретиться снова.</p>")


def test_inject_tasks_appends_when_section_missing():
    summary = "<h2>📌 Кратко</h2><p>Коротко о встрече.</p>"
    tasks = [{"assignee": "", "task": "Позвонить клиенту", "deadline": ""}]
    out = inject_tasks_into_summary(summary, tasks)
    assert out.startswith(summary)
    assert tasks_checklist_html(tasks, completed=False) in out


def test_inject_without_tasks_keeps_summary():
    summary = "<h2>📌 Кратко</h2><p>x</p>"
    assert inject_tasks_into_summary(summary, []) == summary


@pytest.mark.xfail(
    strict=True,
    reason="TODO: при заголовках <b>📋 Задачи</b> разделы после задач теряются "
    "(_NEXT_SECTION_RE в telegram_rich ищет только <h2>/<h3>).",
)
def test_inject_tasks_keeps_next_sections_with_bold_headings():
    summary = (
        "<b>📌 Кратко</b><br><br>Обсудили релиз.<br><br>"
        "<b>📋 Задачи</b><br><br>• Иван — сделать одно<br><br>"
        "<b>🔜 Следующие шаги</b><br><br>Встретиться снова."
    )
    tasks = [{"assignee": "Иван", "task": "Подготовить отчёт", "deadline": ""}]
    out = inject_tasks_into_summary(summary, tasks)
    assert "Встретиться снова." in out
