import unittest

from assistant.integrations.openrouter_client import (
    _payload_for_comet,
    build_gpt_picker_models,
    extract_chat_message_media,
    gpt_display_label,
    model_emits_images,
    normalize_openrouter_models,
    sanitize_openrouter_model_id,
)


class OpenRouterModelsTests(unittest.TestCase):
    def test_sanitize_model_id(self) -> None:
        self.assertEqual(sanitize_openrouter_model_id("openai/gpt-4o-mini"), "openai/gpt-4o-mini")
        self.assertEqual(
            sanitize_openrouter_model_id("minimax/minimax-m2.7:free"),
            "minimax/minimax-m2.7:free",
        )
        self.assertIsNone(sanitize_openrouter_model_id("../etc/passwd"))
        self.assertIsNone(sanitize_openrouter_model_id("bad model"))

    def test_gpt_display_label(self) -> None:
        self.assertEqual(gpt_display_label(None), "GPT")
        self.assertEqual(gpt_display_label("openai/gpt-4.1"), "GPT-4.1")
        self.assertEqual(gpt_display_label("openai/gpt-4o-mini"), "GPT-4o mini")
        self.assertEqual(gpt_display_label("openai/gpt-5.6-sol"), "GPT-5.6 Sol")
        self.assertEqual(gpt_display_label("anthropic/claude-sonnet-4"), "GPT-claude-sonnet-4")

    def test_normalize_filters_non_text_and_keeps_default(self) -> None:
        out = normalize_openrouter_models(
            [
                {
                    "id": "openai/gpt-5.4",
                    "name": "OpenAI: GPT-5.4",
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "openai/text-embedding-3-large",
                    "name": "Embedding",
                    "architecture": {"output_modalities": ["embeddings"]},
                },
                {
                    "id": "google/gemini-2.5-flash",
                    "name": "Google: Gemini 2.5 Flash",
                    "architecture": {"output_modalities": ["text"]},
                },
            ],
            default="google/gemini-2.5-flash",
        )
        ids = [m["id"] for m in out["models"]]
        self.assertEqual(out["default"], "google/gemini-2.5-flash")
        self.assertEqual(ids[0], "google/gemini-2.5-flash")
        self.assertEqual(ids[1], "openai/gpt-5.4")
        self.assertNotIn("openai/text-embedding-3-large", ids)
        names = {m["id"]: m["name"] for m in out["models"]}
        self.assertEqual(names["openai/gpt-5.4"], "GPT-5.4")

    def test_gpt_picker_ignores_gemini_fallback_catalog(self) -> None:
        out = build_gpt_picker_models({"google/gemini-2.5-flash"})
        ids = [m["id"] for m in out["models"]]
        self.assertEqual(out["default"], "openai/gpt-4.1")
        self.assertIn("openai/gpt-5.6-sol", ids)
        self.assertIn("openai/gpt-4o", ids)
        self.assertNotIn("google/gemini-2.5-flash", ids)

    def test_gpt_picker_defaults_to_gpt_41_from_catalog(self) -> None:
        out = build_gpt_picker_models({"openai/gpt-5.4", "openai/gpt-4.1", "openai/gpt-4o"})
        self.assertEqual(out["default"], "openai/gpt-4.1")

    def test_comet_payload_strips_pdf_file_parts(self) -> None:
        out = _payload_for_comet(
            {
                "model": "openai/gpt-4.1",
                "plugins": [{"id": "file-parser"}],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "когда каникулы?"},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "holidays.pdf",
                                    "file_data": "data:application/pdf;base64,AAA",
                                },
                            },
                        ],
                    }
                ],
            }
        )
        self.assertNotIn("plugins", out)
        self.assertIn("когда каникулы?", out["messages"][0]["content"])
        self.assertIn("holidays.pdf", out["messages"][0]["content"])
        self.assertNotIn("file_data", str(out["messages"]))
        self.assertNotIn("modalities", out)

    def test_extract_chat_images_from_message_field(self) -> None:
        data = {
            "choices": [
                {
                    "message": {
                        "content": "вот",
                        "images": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,QUJD"
                                },
                            }
                        ],
                    }
                }
            ]
        }
        text, images = extract_chat_message_media(data)
        self.assertEqual(text, "вот")
        self.assertEqual(images[0]["mime"], "image/png")
        self.assertTrue(images[0]["b64"])

    def test_model_emits_images(self) -> None:
        self.assertTrue(model_emits_images("google/gemini-2.5-flash-image"))
        self.assertTrue(model_emits_images("openai/gpt-image-1"))
        self.assertFalse(model_emits_images("openai/gpt-4.1"))

    def test_gpt_picker_keeps_only_listed_openai_from_full_catalog(self) -> None:
        out = build_gpt_picker_models(
            {"openai/gpt-5.4", "openai/gpt-4o", "openai/gpt-3.5-turbo"},
            ask="openai/gpt-4o",
        )
        ids = [m["id"] for m in out["models"]]
        self.assertEqual(out["default"], "openai/gpt-4o")
        self.assertEqual(set(ids), {"openai/gpt-5.4", "openai/gpt-4o"})


if __name__ == "__main__":
    unittest.main()
