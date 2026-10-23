"""Серверный Markdown → HTML саммари совпадает с NoteHtml.markdownToHtml из приложения."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from assistant.lib.note_markdown import markdown_to_html

ROOT = Path(__file__).resolve().parents[1]

SAMPLES = [
    "## 📌 Кратко\nОбсудили запуск пилота.\n\n## 📋 Задачи\n- [ ] Дима — подготовить API — до пятницы\n- [x] Ира — отправить договор",
    "## ✅ Выполненные задачи\n- Созвон проведён\n\n## 📋 Задачи\n- Вова — **срочно** проверить ~~старый~~ новый план",
    "## 📌 Кратко Обсудили *бюджет* и сроки ## 🎯 Что обсуждали - Бюджет на Q4 - Найм ## 📋 Задачи - [ ] Саша — смета",
    "### Итоги встречи Решили двигаться дальше\nОбычный абзац <b>с тегом</b> & амперсандом \"кавычки\"",
    "## 📊 Метрики\n| Показатель | Значение |\n|---|---|\n| Выручка | 10 |\n| Расходы | 5 |\n\nПосле таблицы",
    "• пункт один\n* пункт два\n1. нумерованный\n\n> цитата",
    "## 🧠 Основные идеи - Идея один. - Идея два - Идея три",
    "",
    "Просто текст без разметки",
]


def _summary_samples() -> list[str]:
    from assistant.lib.summary_render import render_summary_html
    from assistant.lib.telegram_markdown import prepare_summary_markdown

    data = {
        "content_type": "meeting",
        "brief": "Синк по интеграции.\nВторой абзац.",
        "discussion": [{"topic": "API", "points": ["Версия 2", "Сроки"]}],
        "decisions": [{"text": "Запускаем пилот"}],
        "tasks": [
            {"assignee": "Дима", "task": "Подготовить API", "deadline": "до пятницы"},
            {"assignee": "", "task": "Собрать фидбек", "completed": True},
        ],
        "open_questions": ["Кто тестирует?"],
        "next_steps": ["Созвон в среду"],
    }
    return [prepare_summary_markdown(render_summary_html(data))]


def _js(samples: list[str]) -> list[str]:
    script = (
        "const fs=require('fs');global.window={};"
        "eval(fs.readFileSync(process.argv[1],'utf8'));"
        "const inp=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(inp.map(s=>window.NoteHtml.markdownToHtml(s))));"
    )
    out = subprocess.run(
        ["node", "-e", script, str(ROOT / "webapp" / "note-html.js")],
        input=json.dumps(samples),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="нужен node для сверки с приложением")
def test_matches_webapp_converter():
    samples = SAMPLES + _summary_samples()
    for md, want in zip(samples, _js(samples)):
        assert markdown_to_html(md) == want, md


def test_task_section_becomes_checklist():
    html = markdown_to_html("## 📋 Задачи\n- [ ] Дима — API\n- [x] Готово")
    assert html == (
        '<h2>📋 Задачи</h2><ul class="note-task-list"><li><p>Дима — API</p></li>'
        "<li checked><p>Готово</p></li></ul>"
    )


def test_done_tasks_heading_stays_whole():
    html = markdown_to_html("## ✅ Выполненные задачи\n\n- [x] Собрать фидбек")
    assert html.startswith('<h2>✅ Выполненные задачи</h2><ul class="note-task-list"><li checked>')
