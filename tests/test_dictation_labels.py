import usage_server as u


def test_strip_single_speaker_prefix():
    assert u._strip_speaker_labels("SPEAKER_1: Добавить в план.") == "Добавить в план."


def test_strip_formatted_speaker_blocks():
    assert u._strip_speaker_labels("Спикер 1:\nПервая.\n\nСпикер 1:\nВторая.") == "Первая.\n\nВторая."


def test_plain_text_untouched():
    assert u._strip_speaker_labels("  Просто текст: без меток.  ") == "Просто текст: без меток."


def test_strip_mixed_case_without_colon_and_inline():
    assert u._strip_speaker_labels("Speaker_0 Привет. Speaker_1: Как дела?") == "Привет. Как дела?"
    assert u._strip_speaker_labels("[Спикер 2]: Добрый день") == "Добрый день"
