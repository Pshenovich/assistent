import os
import tempfile
import unittest

from assistant.board.context import chair_system_for_meeting, strip_agent_markers
from assistant.stores import share_comments
from assistant.stores import user_agents as agents


class UserAgentsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USER_AGENTS_DB_PATH"] = os.path.join(self._tmpdir.name, "agents.sqlite")
        agents.reset_connection()

    def tearDown(self) -> None:
        agents.reset_connection()
        self._tmpdir.cleanup()

    def test_list_includes_builtins(self) -> None:
        rows = agents.list_agents(1)
        ids = [row["id"] for row in rows]
        self.assertEqual(ids[:3], ["paie", "research", "gpt"])
        self.assertTrue(all(row["builtin"] and not row["can_delete"] for row in rows[:3]))

    def test_create_patch_delete_custom(self) -> None:
        created = agents.create_agent(
            2,
            title="Evaluate",
            prompt="Оцени задачу",
            rules="Вилка часов",
            model="openai/gpt-4.1",
            web_search=True,
            temperature=0.3,
        )
        self.assertEqual(created["kind"], "custom")
        self.assertTrue(created["can_delete"])
        self.assertTrue(created["web_search"])
        listed = agents.list_agents(2)
        self.assertEqual(len(listed), 4)
        patched = agents.patch_agent(2, created["id"], {"title": "Оценка", "web_search": False})
        self.assertEqual(patched["title"], "Оценка")
        self.assertFalse(patched["web_search"])
        agents.delete_agent(2, created["id"])
        self.assertEqual(len(agents.list_agents(2)), 3)

    def test_cannot_delete_builtin(self) -> None:
        with self.assertRaises(agents.AgentForbidden):
            agents.delete_agent(3, "gpt")

    def test_isolation(self) -> None:
        agents.create_agent(10, title="A", model="openai/gpt-4.1")
        self.assertEqual(len(agents.list_agents(11)), 3)
        other = agents.list_agents(10)
        self.assertEqual(len(other), 4)
        with self.assertRaises(agents.AgentNotFound):
            agents.delete_agent(11, other[-1]["id"])

    def test_gpt_overlay_runtime(self) -> None:
        agents.patch_agent(
            4,
            "gpt",
            {"prompt": "Отвечай коротко", "rules": "Без вступлений", "web_search": True},
        )
        cfg = agents.runtime_config(4, "gpt")
        self.assertIn("Отвечай коротко", cfg["system"])
        self.assertIn("Без вступлений", cfg["system"])
        self.assertTrue(cfg["web_search"])
        reset = agents.reset_agent(4, "gpt")
        self.assertFalse(reset["web_search"])
        self.assertEqual(reset["prompt"], agents.builtin_prompt("gpt"))

    def test_research_keeps_web(self) -> None:
        agents.patch_agent(5, "research", {"web_search": False, "model": "openai/gpt-4o-mini"})
        cfg = agents.runtime_config(5, "research")
        self.assertTrue(cfg["web_search"])
        self.assertEqual(cfg["model"], "openai/gpt-4o-mini")

    def test_unknown_agent(self) -> None:
        with self.assertRaises(agents.AgentNotFound):
            agents.runtime_config(6, "missing")

    def test_comment_helpers(self) -> None:
        created = agents.create_agent(7, title="Редактор", model="openai/gpt-4.1")
        prefix = agents.comment_prefix(created["id"])
        self.assertTrue(prefix.startswith("__agent__:"))
        row = {
            "prefix": prefix,
            "author_username": "agent",
            "author_name": "Редактор",
        }
        self.assertTrue(share_comments.is_agent_turn(row))
        self.assertEqual(share_comments.agent_id_of(row), created["id"])
        self.assertFalse(share_comments.is_gpt_turn(row))
        self.assertFalse(share_comments.is_research_turn(row))

    def test_chair_prompt_marker(self) -> None:
        meeting = {
            "extra_instruction": "фон\n[chair_prompt]\nКастомный CHAIR\n[/chair_prompt]"
        }
        self.assertEqual(chair_system_for_meeting(meeting), "Кастомный CHAIR")
        self.assertEqual(strip_agent_markers(meeting["extra_instruction"]), "фон")


if __name__ == "__main__":
    unittest.main()
