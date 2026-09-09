from __future__ import annotations

from assistant.lib.calendar_event_utils import calendar_description_plain


SAMPLE = (
    "<p><strong>Выручка + прибыль</strong><br>каждая задача на встрече должна отвечать на вопрос:</p>"
    "<blockquote><p>👉 <em>Как это влияет на рост выручки или сохранение прибыли?<br></em></p>"
    "<p><em><br></em></p>"
    "<p><strong>1. Цифры (5 минут)</strong></p>"
    "<ul><li><p>выручка,</p></li>"
    "<li><p>MRR / фиксы / переменка,</p></li>"
    "<li><p>что выросло / что упало.</p></li></ul>"
    "<p><strong>2. 1–3 ключевых приоритета недели</strong><br>Каждый приоритет:</p>"
    "<ul><li><p>что это,</p></li>"
    "<li><p><strong>как влияет на деньги</strong>,</p></li>"
    "<li><p>кто владелец.</p></li></ul></blockquote>"
)


def test_calendar_description_plain_strips_gcal_html() -> None:
    text = calendar_description_plain(SAMPLE)
    assert "<p>" not in text
    assert "<strong>" not in text
    assert "Выручка + прибыль" in text
    assert "• выручка," in text
    assert "• как влияет на деньги" in text


def test_calendar_description_plain_keeps_plain_text() -> None:
    assert calendar_description_plain("Просто текст\nвторая строка") == (
        "Просто текст\nвторая строка"
    )
