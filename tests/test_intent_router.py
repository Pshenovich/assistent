"""Тесты слияния regex + LLM маршрутов (без вызова OpenRouter)."""

import unittest

from assistant.nlu.intent_router import Route, resolve_route


class TestResolveRoute(unittest.TestCase):
    def test_llm_only_above_min(self) -> None:
        llm = Route(skill="calendar", sub_intent="update", confidence=0.9, source="llm")
        out = resolve_route(None, llm)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.skill, "calendar")
        self.assertEqual(out.source, "llm")

    def test_llm_only_below_min(self) -> None:
        llm = Route(skill="calendar", sub_intent="update", confidence=0.3, source="llm")
        self.assertIsNone(resolve_route(None, llm))

    def test_conflict_prefers_llm_at_min_confidence(self) -> None:
        regex = Route(skill="calendar", sub_intent="free", confidence=1.0, source="regex")
        llm = Route(
            skill="calendar",
            sub_intent="update",
            confidence=0.7,
            source="llm",
            calendar_kind="update",
        )
        out = resolve_route(regex, llm)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.sub_intent, "update")
        self.assertEqual(out.source, "llm")

    def test_conflict_low_llm_keeps_regex(self) -> None:
        regex = Route(skill="reminder", sub_intent="create", confidence=1.0, source="regex")
        llm = Route(skill="calendar", sub_intent="update", confidence=0.5, source="llm")
        out = resolve_route(regex, llm)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.skill, "reminder")

    def test_agree_merges_llm_body(self) -> None:
        regex = Route(
            skill="calendar",
            sub_intent="update",
            body="",
            confidence=1.0,
            source="regex",
        )
        llm = Route(
            skill="calendar",
            sub_intent="update",
            body="перенеси встречу про тест на 18:00",
            confidence=0.95,
            source="llm",
        )
        out = resolve_route(regex, llm)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("тест", out.body)


class TestRouterEnabledDefault(unittest.TestCase):
    def test_default_on_unless_zero(self) -> None:
        import os
        from assistant.nlu.intent_router import router_enabled

        old = os.environ.pop("INTENT_ROUTER_ENABLED", None)
        try:
            self.assertTrue(router_enabled())
        finally:
            if old is not None:
                os.environ["INTENT_ROUTER_ENABLED"] = old

    def test_explicit_off(self) -> None:
        import os
        from assistant.nlu.intent_router import router_enabled

        os.environ["INTENT_ROUTER_ENABLED"] = "0"
        try:
            self.assertFalse(router_enabled())
        finally:
            os.environ.pop("INTENT_ROUTER_ENABLED", None)


if __name__ == "__main__":
    unittest.main()
