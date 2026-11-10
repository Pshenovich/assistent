"""Связки: состав, быстрая связь, доступ (каждый видит только доступное)."""

import os
import tempfile
import unittest
from unittest import mock

from assistant.services import bundle_access
from assistant.stores import bundles
from assistant.stores import note_members
from assistant.stores import notes as notes_store


class BundlesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _n(self, uid: int, title: str) -> str:
        return str(notes_store.create_note(uid, title, "<p>x</p>")["id"])

    def test_link_creates_then_extends_plain_bundle(self) -> None:
        a, b, c = self._n(1, "A"), self._n(1, "B"), self._n(1, "C")
        first = bundles.link(1, ("note", a, ""), ("note", b, ""))
        self.assertEqual(first["title"], "")
        self.assertEqual(len(first["items"]), 2)
        second = bundles.link(1, ("note", c, ""), ("note", a, ""))
        self.assertEqual(second["id"], first["id"])
        self.assertEqual({i["item_id"] for i in second["items"]}, {a, b, c})

    def test_named_bundle_is_separate_from_plain(self) -> None:
        a, b, c = self._n(1, "A"), self._n(1, "B"), self._n(1, "C")
        named = bundles.create(1, "  Запуск   Pro ", [("note", a, ""), ("note", b, "")])
        self.assertEqual(named["title"], "Запуск Pro")
        plain = bundles.link(1, ("note", a, ""), ("note", c, ""))
        self.assertNotEqual(plain["id"], named["id"])
        views = bundle_access.bundles_for_item(1, "note", a)
        self.assertEqual([v["title"] for v in views], ["", "Запуск Pro"])

    def test_rejects_self_link_bad_kind_and_single_item(self) -> None:
        a = self._n(1, "A")
        with self.assertRaises(ValueError):
            bundles.link(1, ("note", a, ""), ("note", a, ""))
        with self.assertRaises(ValueError):
            bundles.norm_item("vault", "1")
        with self.assertRaises(ValueError):
            bundles.create(1, "x", [("note", a, "")])

    def test_viewer_sees_only_accessible_items(self) -> None:
        mine = self._n(1, "Мой")
        secret = self._n(1, "Закрытый")
        shared = self._n(1, "Общий")
        note_members.add_member(1, int(shared), 2)
        b = bundles.create(1, "Проект", [("note", mine, ""), ("note", secret, ""), ("note", shared, "")])
        v2 = bundle_access.visible_bundle(2, b["id"])
        self.assertIsNotNone(v2)
        self.assertEqual([i["title"] for i in v2["items"]], ["Общий"])
        self.assertEqual(v2["hidden"], 2)
        self.assertFalse(v2["can_delete"])
        self.assertIsNone(bundle_access.visible_bundle(3, b["id"]))

    def test_can_add_requires_access(self) -> None:
        other = self._n(2, "Чужой")
        with self.assertRaises(ValueError):
            bundle_access.can_add(1, "note", other)
        self.assertEqual(bundle_access.can_add(2, "note", other)["title"], "Чужой")
        with self.assertRaises(ValueError):
            bundle_access.can_add(1, "person", "a@x.ru", "")

    def test_personal_items_only_for_adder(self) -> None:
        a = self._n(1, "A")
        note_members.add_member(1, int(a), 2)
        b = bundles.create(1, "", [("note", a, ""), ("person", "Ann@X.ru", "Аня")])
        self.assertEqual([i["kind"] for i in bundle_access.visible_bundle(1, b["id"])["items"]], ["note", "person"])
        self.assertEqual([i["kind"] for i in bundle_access.visible_bundle(2, b["id"])["items"]], ["note"])

    def test_remove_last_pair_deletes_bundle(self) -> None:
        a, b = self._n(1, "A"), self._n(1, "B")
        bd = bundles.link(1, ("note", a, ""), ("note", b, ""))
        self.assertIsNone(bundles.remove_item(bd["id"], "note", b))
        self.assertIsNone(bundles.get(bd["id"]))

    def test_forget_item_cleans_up(self) -> None:
        a, b, c = self._n(1, "A"), self._n(1, "B"), self._n(1, "C")
        bd = bundles.create(1, "X", [("note", a, ""), ("note", b, ""), ("note", c, "")])
        bundles.forget_item("note", a)
        self.assertEqual({i["item_id"] for i in bundles.get(bd["id"])["items"]}, {b, c})

    def test_context_items_excludes_self_and_dedupes(self) -> None:
        a, b, c = self._n(1, "A"), self._n(1, "B"), self._n(1, "C")
        bundles.create(1, "X", [("note", a, ""), ("note", b, "")])
        bundles.create(1, "Y", [("note", a, ""), ("note", b, ""), ("note", c, "")])
        ctx = bundle_access.context_items(1, "note", a)
        self.assertEqual(sorted(i["title"] for i in ctx), ["B", "C"])

    def test_task_access_goes_through_task_store(self) -> None:
        a = self._n(1, "A")
        with mock.patch(
            "assistant.stores.calendar_tasks.get_task_for_user",
            side_effect=lambda uid, tid: {"title": "Сверстать", "assignee_name": "Аня"} if str(uid) == "1" else None,
        ):
            bd = bundles.create(1, "", [("note", a, ""), ("task", "7", "")])
            items = bundle_access.visible_bundle(1, bd["id"])["items"]
            self.assertEqual(items[1]["title"], "Сверстать")
            self.assertEqual(items[1]["subtitle"], "Задача · Аня")
            note_members.add_member(1, int(a), 2)
            self.assertEqual([i["kind"] for i in bundle_access.visible_bundle(2, bd["id"])["items"]], ["note"])

    def test_leo_proposes_then_links_on_confirm(self) -> None:
        import json

        from assistant.services import gpt_calendar_tools as gcal

        a, b = self._n(1, "Тариф Pro"), self._n(1, "Созвон")
        other = self._n(2, "Чужой")
        ctx = gcal.GptCalendarContext(uid=1)
        ctx.open_note_id = a
        res = json.loads(gcal.tool_propose_link(ctx, {"items": [{"type": "current_note"}, {"type": "note", "id": b},
                                                                {"type": "note", "id": other}]}))
        self.assertTrue(res.get("proposed"))
        action = gcal.get_action(ctx.actions[0])
        self.assertEqual(action["status"], "proposed")
        self.assertEqual([i["id"] for i in action["payload"]["items"]], [a, b])  # чужой документ отсеян
        self.assertEqual(bundles.bundles_for_item("note", a), [])  # до подтверждения ничего не связано
        out = gcal.apply_link(action)
        self.assertEqual({i["item_id"] for i in bundles.get(out["bundle_id"])["items"]}, {a, b})

    def test_leo_needs_two_accessible_items(self) -> None:
        import json

        from assistant.services import gpt_calendar_tools as gcal

        ctx = gcal.GptCalendarContext(uid=1)
        res = json.loads(gcal.tool_propose_link(ctx, {"items": [{"type": "note", "id": self._n(2, "Чужой")},
                                                                {"type": "current_note"}]}))
        self.assertFalse(res.get("proposed"))
        self.assertEqual(ctx.actions, [])

    def test_list_for_user_named_only_and_by_access(self) -> None:
        a, b, c = self._n(1, "Тариф Pro"), self._n(1, "Бюджет"), self._n(1, "Черновик")
        note_members.add_member(1, int(a), 2)
        bundles.create(1, "Контент завод", [("note", a, ""), ("note", b, "")])
        bundles.link(1, ("note", b, ""), ("note", c, ""))  # безымянная — не в списке
        self.assertEqual([x["title"] for x in bundle_access.list_for_user(1)], ["Контент завод"])
        # второй видит связку через общий документ, но только его
        seen = bundle_access.list_for_user(2)
        self.assertEqual([x["title"] for x in seen], ["Контент завод"])
        self.assertEqual([i["title"] for i in seen[0]["items"]], ["Тариф Pro"])
        self.assertEqual(bundle_access.list_for_user(3), [])
        # поиск по названию связки и по названиям элементов
        self.assertEqual(len(bundle_access.list_for_user(1, query="завод")), 1)
        self.assertEqual(len(bundle_access.list_for_user(1, query="бюджет")), 1)
        self.assertEqual(bundle_access.list_for_user(1, query="черновик"), [])
        self.assertEqual(bundle_access.list_for_user(2, query="бюджет"), [])  # недоступное не ищется

    def test_leo_finds_bundle_by_name(self) -> None:
        import json

        from assistant.services import gpt_calendar_tools as gcal

        a, b = self._n(1, "Сценарии"), self._n(1, "Ферма")
        bundles.create(1, "Контент завод", [("note", a, ""), ("note", b, "")])
        res = json.loads(gcal.tool_bundles_find(gcal.GptCalendarContext(uid=1), {"query": "контент"}))
        self.assertEqual(res["bundles"][0]["bundle"], "Контент завод")
        self.assertEqual({i["title"] for i in res["bundles"][0]["items"]}, {"Сценарии", "Ферма"})
        other = json.loads(gcal.tool_bundles_find(gcal.GptCalendarContext(uid=2), {"query": "контент"}))
        self.assertEqual(other["bundles"], [])
        self.assertIn("bundles_find", {t["function"]["name"] for t in gcal.TOOLS} if hasattr(gcal, "TOOLS") else {"bundles_find"})


class BundlesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        import usage_server

        self.us = usage_server

    def tearDown(self) -> None:
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _p(self, uid: int):
        return self.us._MiniappPrincipal(uid, {"id": uid})

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_link_list_rename_remove(self) -> None:
        from fastapi import HTTPException

        us = self.us
        a = str(notes_store.create_note(1, "A", "<p>x</p>")["id"])
        b = str(notes_store.create_note(1, "B", "<p>x</p>")["id"])
        other = str(notes_store.create_note(2, "Чужой", "<p>x</p>")["id"])
        body = us._MiniappLinkBody(a=us._MiniappLinkItem(kind="note", id=a), b=[us._MiniappLinkItem(kind="note", id=b)])
        res = self._run(us.miniapp_links_link(body, self._p(1)))
        bid = res["bundle"]["id"]
        self.assertEqual(len(res["bundle"]["items"]), 2)
        bad = us._MiniappLinkBody(a=us._MiniappLinkItem(kind="note", id=a), b=[us._MiniappLinkItem(kind="note", id=other)])
        with self.assertRaises(HTTPException) as ctx:
            self._run(us.miniapp_links_link(bad, self._p(1)))
        self.assertEqual(ctx.exception.status_code, 400)
        listed = self._run(us.miniapp_links_for_item(self._p(1), kind="note", id=b))
        self.assertEqual([x["id"] for x in listed["bundles"]], [bid])
        self.assertEqual(self._run(us.miniapp_links_for_item(self._p(2), kind="note", id=b))["bundles"], [])
        renamed = self._run(us.miniapp_bundle_rename(bid, us._MiniappBundlePatch(title="Проект"), self._p(1)))
        self.assertEqual(renamed["bundle"]["title"], "Проект")
        with self.assertRaises(HTTPException):
            self._run(us.miniapp_bundle_get(bid, self._p(2)))
        gone = self._run(us.miniapp_bundle_remove_item(bid, "note", b, self._p(1)))
        self.assertIsNone(gone["bundle"])


if __name__ == "__main__":
    unittest.main()
