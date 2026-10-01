from assistant.nlu import llm
from assistant.nlu.ask_context import assemble_ask_messages, build_context_prefix, clip_text


def test_answer_with_context_forwards_history(monkeypatch):
    seen: dict = {}

    def fake_result(system, user, **kwargs):
        seen["history"] = kwargs.get("history")
        seen["user"] = user
        seen["system"] = system
        return "ok", []

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    out = llm.answer_with_context(
        "второй вопрос",
        "заметка",
        history=[{"role": "user", "content": "первый вопрос"}],
    )
    assert out == "ok"
    assert seen["history"][0]["content"] == "первый вопрос"
    assert "второй вопрос" in seen["user"]
    assert ":::file" in seen["system"]


def test_answer_with_context_puts_note_in_system(monkeypatch):
    seen: dict = {}

    def fake_result(system, user, **kwargs):
        seen["system"] = system
        seen["user"] = user
        seen["context_prefix"] = kwargs.get("context_prefix")
        return "ok", []

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    out = llm.answer_with_context(
        "что решили?",
        "",
        note_title="Встреча",
        note_text="Договорились о пилоте",
        knowledge_brief="Каталог:\n• Боты — 10 ₽",
        quote="пилот",
    )
    assert out == "ok"
    assert "что решили?" in seen["user"]
    prefix = seen["context_prefix"] or ""
    assert "БАЗА ЗНАНИЙ:" in prefix
    assert "ЗАМЕТКА:" in prefix
    assert "ВЫДЕЛЕННЫЙ ТЕКСТ:" not in prefix
    assert "пилот" in seen["user"]
    assert "Договорились о пилоте" in prefix
    assert prefix.index("БАЗА ЗНАНИЙ:") < prefix.index("ЗАМЕТКА:")


def test_clip_and_prefix_budgets():
    long_note = "x" * 9000
    prefix = build_context_prefix(note_title="T", note_text=long_note, quote="q" * 800)
    assert "ЗАМЕТКА:" in prefix
    assert len(clip_text(long_note, 8000)) == 8000
    extra, user = assemble_ask_messages(question="hi", note_text="тело", quote="фрагмент")
    assert extra.startswith("ЗАМЕТКА:")
    assert "фрагмент" in user
    assert "ВЫДЕЛЕННЫЙ ТЕКСТ:" in user
    assert "фрагмент" not in extra


def test_assemble_ask_messages_multiple_quotes():
    extra, user = assemble_ask_messages(
        question="сравни",
        note_text="тело",
        quotes=["первый фрагмент", "второй фрагмент"],
    )
    assert extra.startswith("ЗАМЕТКА:")
    assert "ВЫДЕЛЕННЫЕ ФРАГМЕНТЫ:" in user
    assert "«первый фрагмент»" in user
    assert "«второй фрагмент»" in user
    assert "Вопрос:\nсравни" in user
    assert "первый фрагмент" not in extra


def test_answer_with_context_sends_pdf_as_openrouter_file(monkeypatch):
    seen: dict = {}

    def fake_complete(payload, **kwargs):
        seen["payload"] = payload
        return {"choices": [{"message": {"content": "каникулы 1–10 января"}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake_complete)
    out = llm.answer_with_context_result(
        "когда каникулы?",
        files=[{"filename": "holidays.pdf", "mime": "application/pdf", "b64": "JVBERg=="}],
    )
    assert "каникулы" in out["answer"]
    user = seen["payload"]["messages"][-1]["content"]
    assert isinstance(user, list)
    file_part = next(p for p in user if p.get("type") == "file")
    assert file_part["file"]["filename"] == "holidays.pdf"
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert seen["payload"]["plugins"] == [{"id": "file-parser"}]


def test_answer_with_context_keeps_model_images(monkeypatch):
    def fake_result(system, user, **kwargs):
        return "вот фото", [{"mime": "image/png", "b64": "aaa"}]

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    out = llm.answer_with_context_result("опиши картинку")
    assert out["answer"] == "вот фото"
    assert out["images"] == [{"mime": "image/png", "b64": "aaa"}]


def test_answer_with_context_generates_image_from_block(monkeypatch):
    seen: dict = {}

    def fake_result(system, user, **kwargs):
        return 'Логотип готов\n\n:::image prompt="red cat logo"\n:::\n', []

    def fake_images(prompt, **kwargs):
        seen["prompt"] = prompt
        return [{"mime": "image/png", "b64": "YmFiYQ=="}]

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    monkeypatch.setattr(
        "assistant.integrations.openrouter_client.openrouter_generate_images",
        fake_images,
    )
    out = llm.answer_with_context_result("сделай логотип")
    assert "Логотип готов" in out["answer"]
    assert ":::image" not in out["answer"]
    assert seen["prompt"] == "red cat logo"
    assert out["images"][0]["b64"] == "YmFiYQ=="


def test_answer_with_context_image_intent_fallback(monkeypatch):
    seen: list[str] = []

    def fake_result(system, user, **kwargs):
        return "Сейчас нарисую.", []

    def fake_images(prompt, **kwargs):
        seen.append(prompt)
        return [{"mime": "image/png", "b64": "Y2F0"}]

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    monkeypatch.setattr(
        "assistant.integrations.openrouter_client.openrouter_generate_images",
        fake_images,
    )
    out = llm.answer_with_context_result("нарисуй кота в очках")
    assert seen
    assert "кота" in seen[0]
    assert out["images"]


def test_answer_with_context_replaces_base64_offer(monkeypatch):
    seen: dict = {}

    def fake_result(system, user, **kwargs):
        return (
            "Похоже, вложения всё ещё не проходят. Могу выслать PNG в виде base64 "
            "(разобью на части, вы сохраните как IMG_5158_3x.png). Подтвердите «да».",
            [],
        )

    def fake_images(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["source"] = kwargs.get("source_images")
        return [{"mime": "image/png", "b64": "Y2F0"}]

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    monkeypatch.setattr(
        "assistant.integrations.openrouter_client.openrouter_generate_images",
        fake_images,
    )
    out = llm.answer_with_context_result(
        "апскейл ×3",
        images=[{"mime": "image/png", "b64": "c3Jj"}],
    )
    assert seen["prompt"]
    assert seen["source"]
    assert out["images"][0]["b64"] == "Y2F0"
    assert out["images"][0]["filename"] == "IMG_5158_3x.png"
    assert "base64" not in out["answer"].lower()
    assert "вложении" in out["answer"]


def test_answer_with_context_no_fake_ready_without_image(monkeypatch):
    def fake_result(system, user, **kwargs):
        return "Готово — файл во вложении.", []

    def fake_images(prompt, **kwargs):
        return []

    monkeypatch.setattr(llm, "_chat_result", fake_result)
    monkeypatch.setattr(
        "assistant.integrations.openrouter_client.openrouter_generate_images",
        fake_images,
    )
    out = llm.answer_with_context_result("еще раз отправь файл")
    assert not out["images"]
    assert "Не удалось" in out["answer"]
    assert "во вложении" not in out["answer"]


def test_chat_merges_context_prefix_into_system(monkeypatch):
    assert seen
    assert "кота" in seen[0]
    assert out["images"]


def test_chat_merges_context_prefix_into_system(monkeypatch):
    seen: dict = {}

    def fake_complete(payload, **kwargs):
        seen["messages"] = payload["messages"]
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake_complete)
    out = llm._chat("SYS", "вопрос", operation="ask", context_prefix="БАЗА ЗНАНИЙ:\nX")
    assert out == "ok"
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][0]["content"].startswith("SYS")
    assert "БАЗА ЗНАНИЙ:\nX" in seen["messages"][0]["content"]
    assert seen["messages"][-1]["content"] == "вопрос"

