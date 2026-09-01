"""Тесты умного поиска задач Bitrix24."""

import unittest

from assistant.services import bitrix_mcp_search as search_mod


class TestBitrixMcpSearch(unittest.TestCase):
    def test_extract_keywords_cordexa(self) -> None:
        kws = search_mod.extract_search_keywords("дай описание задачи по переносу на кордекса")
        joined = " ".join(kws).lower()
        self.assertIn("кордекса", joined)
        self.assertIn("перенос", joined)

    def test_extract_keywords_boostra_llm(self) -> None:
        kws = search_mod.extract_search_keywords("найди задачи про бустра ллм")
        joined = " ".join(kws).lower()
        self.assertIn("бустра llm", joined)

    def test_extract_search_queries_phrase(self) -> None:
        queries = search_mod.extract_search_queries("найди задачи про перенос на кордекса")
        joined = " ".join(queries).lower()
        self.assertIn("перенос", joined)
        self.assertIn("кордекса", joined)

    def test_extract_search_queries_payment_in_bot(self) -> None:
        queries = search_mod.extract_search_queries("Найди в задачах про оплату в боте")
        self.assertIn("оплату в боте", [q.lower() for q in queries])
        joined = " ".join(queries).lower()
        self.assertIn("оплат", joined)
        self.assertIn("бот", joined)

    def test_extract_search_queries_zoom(self) -> None:
        queries = search_mod.extract_search_queries("поиск задач про zoom интеграцию")
        joined = " ".join(queries).lower()
        self.assertIn("zoom", joined)

    def test_is_task_list_request(self) -> None:
        self.assertTrue(search_mod.is_task_list_request("найди задачи про бустра ллм"))
        self.assertTrue(search_mod.is_task_list_request("Найди в задачах про оплату в боте"))
        self.assertFalse(search_mod.is_task_list_request("дай описание задачи по кордекса"))

    def test_has_clear_winner_false_for_list(self) -> None:
        candidates = [
            {"taskId": 1, "title": "A", "score": 30},
            {"taskId": 2, "title": "B", "score": 28},
        ]
        self.assertFalse(search_mod.has_clear_winner(candidates, "найди задачи про бустра"))

    def test_score_prefers_transfer_task(self) -> None:
        keywords = search_mod.extract_search_keywords("дай описание задачи по переносу на кордекса")
        queries = search_mod.extract_search_queries("дай описание задачи по переносу на кордекса")
        score = search_mod._score_title(
            "[Бустра LLM]Перенос BoostraGPT -> Корdекса",
            keywords,
            "дай описание задачи по переносу на кордекса",
            queries,
        )
        noise = search_mod._score_title(
            "[Платформа] Обезличивание персональных данных",
            keywords,
            "дай описание задачи по переносу на кордекса",
            queries,
        )
        self.assertGreater(score, noise)

    def test_score_payment_in_bot_task(self) -> None:
        user_text = "Найди в задачах про оплату в боте"
        keywords = search_mod.extract_search_keywords(user_text)
        queries = search_mod.extract_search_queries(user_text)
        title = "[Бустра/Боты авториз]Оплата займа внутри чата по запросу реквизитов"
        score = search_mod._score_title(title, keywords, user_text, queries)
        self.assertGreaterEqual(score, search_mod._min_match_score(user_text))

    def test_score_accepts_partial_phrase_match(self) -> None:
        keywords = search_mod.extract_search_keywords("найди задачи про zoom")
        queries = search_mod.extract_search_queries("найди задачи про zoom")
        score = search_mod._score_title(
            "[Platform] Zoom SDK integration",
            keywords,
            "найди задачи про zoom",
            queries,
        )
        self.assertGreaterEqual(score, search_mod._min_match_score("найди задачи про zoom"))

    def test_has_clear_winner(self) -> None:
        candidates = [
            {"taskId": 22749, "title": "[Бустра LLM]Перенос BoostraGPT -> Корdекса", "score": 57},
            {"taskId": 15793, "title": "[Платформа] Обезличивание", "score": 12},
        ]
        self.assertTrue(
            search_mod.has_clear_winner(
                candidates,
                "дай описание задачи по переносу на кордекса",
            )
        )
