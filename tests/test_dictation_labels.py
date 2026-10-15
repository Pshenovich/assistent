import usage_server as u


def test_strip_single_speaker_prefix():
    assert u._strip_speaker_labels("SPEAKER_1: Добавить в план.") == "Добавить в план."


def test_strip_formatted_speaker_blocks():
    assert u._strip_speaker_labels("Спикер 1:\nПервая.\n\nСпикер 1:\nВторая.") == "Первая.\nВторая."


def test_plain_text_untouched():
    assert u._strip_speaker_labels("  Просто текст: без меток.  ") == "Просто текст: без меток."
