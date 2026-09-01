from assistant.lib.summary_enrich import (
    inject_tasks_into_summary,
    merge_tasks,
    tasks_section_html,
)


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


def test_inject_tasks_replaces_short_tasks_section():
    summary = (
        "<b>📌 Кратко</b><br><br>Обсудили релиз.<br><br>"
        "<b>📋 Задачи</b><br><br>• Иван — сделать одно<br><br>"
        "<b>🔜 Следующие шаги</b><br><br>Встретиться снова."
    )
    tasks = [
        {"assignee": "Иван", "task": "Подготовить отчёт", "deadline": ""},
        {"assignee": "Мария", "task": "Согласовать бюджет", "deadline": "пятница"},
        {"assignee": "", "task": "Отправить договор", "deadline": ""},
        {"assignee": "Пётр", "task": "Обновить roadmap", "deadline": ""},
    ]
    out = inject_tasks_into_summary(summary, tasks)
    assert out.count("• ") == 4
    assert "Согласовать бюджет" in out
    assert "Обновить roadmap" in out
    assert "сделать одно" not in out
    assert "<b>🔜 Следующие шаги</b>" in out


def test_inject_tasks_appends_when_section_missing():
    summary = "<b>📌 Кратко</b><br><br>Коротко о встрече."
    tasks = [{"assignee": "", "task": "Позвонить клиенту", "deadline": ""}]
    out = inject_tasks_into_summary(summary, tasks)
    assert tasks_section_html(tasks) in out
