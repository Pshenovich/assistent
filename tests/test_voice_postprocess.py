from assistant.integrations.transcribe import TranscribeResult
from assistant.lib.voice_postprocess import (
    apply_dictionary,
    match_snippet,
    numbers_to_digits,
    postprocess,
    remove_fillers,
)
from assistant.stores import voice_prefs


def test_fillers_example_from_govorii():
    src = (
        "Ну, короче, Я посмотрел договор, и, э-э, там в пятом пункте как бы не тот "
        "срок оплаты. Давай, ну, поправим до пятницы, короче."
    )
    assert remove_fillers(src) == (
        "Я посмотрел договор, и там в пятом пункте как бы не тот срок оплаты. "
        "Давай поправим до пятницы."
    )


def test_fillers_keep_meaningful_words():
    assert remove_fillers("Вот договор. Как бы не опоздать.") == "Вот договор. Как бы не опоздать."
    assert remove_fillers("ну давай завтра") == "Давай завтра"
    assert remove_fillers("Он сказал, ну, что придёт, вот.") == "Он сказал, что придёт."


def test_numbers():
    assert numbers_to_digits("встреча завтра в шесть часов вечера") == "встреча завтра в 6 часов вечера"
    assert numbers_to_digits("двадцать пятого марта в шесть тридцать") == "25 марта в 6:30"
    assert numbers_to_digits("в девять ноль пять") == "в 9:05"
    assert numbers_to_digits("сто двадцать пять тысяч рублей") == "125 000 рублей"
    assert numbers_to_digits("две тысячи двадцать шесть") == "2026"
    assert numbers_to_digits("купи два-три яблока") == "купи 2-3 яблока"


def test_numbers_leave_ambiguous_words():
    assert numbers_to_digits("один из нас") == "один из нас"
    assert numbers_to_digits("у меня одна задача") == "у меня одна задача"
    assert numbers_to_digits("тысячи людей") == "тысячи людей"
    assert numbers_to_digits("приеду с семью") == "приеду с семью"
    assert numbers_to_digits("двадцать пятого числа") == "двадцать пятого числа"


def test_dictionary_with_endings_and_yo():
    entries = [
        {"from": "кубер", "to": "Kubernetes"},
        {"from": "гитхаб", "to": "GitHub"},
        {"from": "лёша", "to": "Алексей"},
    ]
    assert (
        apply_dictionary("Залей на гитхабе конфиг кубера, леша", entries)
        == "Залей на GitHub конфиг Kubernetes, Алексей"
    )
    # Кириллическая замена — без окончаний, чтобы не ломать слова.
    assert apply_dictionary("лешаков", entries) == "лешаков"


def test_snippet_only_whole_phrase():
    sn = [{"trigger": "адрес офиса", "text": "Москва, ул. Примерная, 1"}]
    assert match_snippet("Адрес офиса.", sn) == "Москва, ул. Примерная, 1"
    assert match_snippet("Пришли адрес офиса клиенту", sn) is None
    assert postprocess("Ну, адрес офиса.", snippets=sn) == "Москва, ул. Примерная, 1"


def test_settings_toggles_and_modes():
    uid = 777001
    voice_prefs.update_settings(
        uid,
        {
            "dictionary_entries": [{"from": "гитхаб", "to": "GitHub"}, {"from": "", "to": "x"}],
            "snippet_entries": [{"trigger": "моя подпись", "text": "С уважением, Вова"}],
        },
    )
    s = voice_prefs.get_settings(uid)
    assert all(s[k] for k in voice_prefs.FEATURES)
    assert s["dictionary_entries"] == [{"from": "гитхаб", "to": "GitHub"}]

    raw = "Ну, залей на гитхаб в шесть"
    assert voice_prefs.process_text(uid, raw) == "Залей на GitHub в 6"
    assert voice_prefs.process_text(uid, "моя подпись") == "С уважением, Вова"
    assert voice_prefs.process_text(uid, "моя подпись", mode="command") == "Моя подпись"
    # Встречи — дословно, только словарь.
    assert voice_prefs.process_text(uid, raw, mode="transcript") == "Ну, залей на GitHub в шесть"

    voice_prefs.update_settings(uid, {"cleanup": False, "numbers": False, "dictionary": False})
    assert voice_prefs.process_text(uid, raw) == raw


def test_transcript_result_dictionary():
    uid = 777002
    voice_prefs.update_settings(uid, {"dictionary_entries": [{"from": "ромашка", "to": "«Ромашка»"}]})
    res = voice_prefs.process_transcript_result(
        uid, TranscribeResult("ну, ромашка", "Спикер 1:\nну, ромашка")
    )
    assert res.plain_text == "ну, «Ромашка»"
    assert res.formatted_text == "Спикер 1:\nну, «Ромашка»"


def test_history_respects_toggle():
    uid = 777003
    voice_prefs.clear_history(uid)
    voice_prefs.add_history(uid, text="первая", source="note")
    voice_prefs.add_history(uid, text="вторая", source="telegram")
    items = voice_prefs.list_history(uid)
    assert [x["text"] for x in items] == ["вторая", "первая"]
    voice_prefs.delete_history_item(uid, items[0]["id"])
    assert [x["text"] for x in voice_prefs.list_history(uid)] == ["первая"]
    voice_prefs.update_settings(uid, {"history": False})
    voice_prefs.add_history(uid, text="третья")
    assert [x["text"] for x in voice_prefs.list_history(uid)] == ["первая"]
    voice_prefs.clear_history(uid)
    assert voice_prefs.list_history(uid) == []
