import unittest

from assistant.integrations.openrouter_client import (
    build_gpt_picker_models,
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
