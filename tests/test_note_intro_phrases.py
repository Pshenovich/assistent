"""«Закинь / запиши / сохрани в заметки …» — создание заметки и в режиме агента Лео."""

import pytest

from assistant.nlu import regex as r


@pytest.mark.parametrize(
    "text,body",
    [
        ("Закинь в заметки: Вебапп открывается только без впн", "Вебапп открывается только без впн"),
        ("запиши в заметки: тест", "тест"),
        ("Добавь в заметки: тест", "тест"),
        ("Сохрани в документ идею про лендинг", "идею про лендинг"),
        ("создай заметку план на неделю", "план на неделю"),
        ("заметка: тест", "тест"),
    ],
)
def test_note_intro(text, body):
    assert r.parse_note_intent(text) == body


@pytest.mark.parametrize("text", ["запиши встречу на завтра", "найди в заметках про X", "напомни в 18:00"])
def test_not_note(text):
    assert r.parse_note_intent(text) is None
