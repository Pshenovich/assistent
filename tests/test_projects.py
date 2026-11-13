"""Проекты: участники и роли, доступ через отдел/организацию, архив, «проект записи», маршруты /projects."""

import os
import tempfile
import unittest
from unittest import mock

from assistant.services import bundle_access
from assistant.stores import bundles, note_members, projects
from assistant.stores import notes as notes_store


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        # организаций в тестах нет, если не сказано иначе
        self._orgs = mock.patch("assistant.stores.orgs_store.orgs_for_user", return_value=[])
        self._orgs.start()

    def tearDown(self) -> None:
        self._orgs.stop()
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _n(self, uid: int, title: str) -> str:
        return str(notes_store.create_note(uid, title, "<p>x</p>")["id"])


class ProjectsStoreTests(_Base):
    def test_create_empty_project_owner_sees_it(self) -> None:
        p = projects.create(1, "  БГПТ  ", description="Клиент", color="blue")
        self.assertEqual(p["title"], "БГПТ")
        self.assertEqual(projects.role_of(1, p), "owner")
        self.assertEqual(projects.role_of(2, p), "")
        v = bundle_access.visible_bundle(1, p["id"])
        assert v is not None
        self.assertTrue(v["is_project"])
        self.assertEqual(v["items"], [])
        self.assertEqual(v["status"], "active")
        self.assertIsNone(bundle_access.visible_bundle(2, p["id"]))
        with self.assertRaises(ValueError):
            projects.create(1, "   ")

    def test_roles_viewer_editor_owner(self) -> None:
        p = projects.create(1, "П")
        projects.set_member(1, p["id"], "u:2", "viewer")
        projects.set_member(1, p["id"], "u:3", "editor")
        with self.assertRaises(ValueError):
            projects.update(2, p["id"], {"title": "Х"})  # смотрящий не правит
        self.assertEqual(projects.update(3, p["id"], {"status": "paused"})["status"], "paused")
        with self.assertRaises(ValueError):
            projects.set_member(3, p["id"], "u:4", "owner")  # владельцев назначает владелец
        with self.assertRaises(ValueError):
            projects.update(3, p["id"], {"archived": True})  # архив — владелец
        with self.assertRaises(ValueError):
            projects.delete(3, p["id"])
        with self.assertRaises(ValueError):
            projects.remove_member(1, p["id"], "u:1")  # последний владелец
        projects.remove_member(2, p["id"], "u:2")  # уйти сам может каждый
        self.assertEqual(projects.role_of(2, projects.get(p["id"])), "")
        with self.assertRaises(ValueError):
            projects.update(1, p["id"], {"status": "x"})

    def test_lead_is_at_least_editor(self) -> None:
        p = projects.create(1, "П", lead_user_id=5)
        self.assertEqual(projects.role_of(5, projects.get(p["id"])), "editor")
        self.assertIn(p["id"], projects.ids_for_user(5))

    def test_group_and_all_only_for_org_projects(self) -> None:
        p = projects.create(1, "Личный")
        with self.assertRaises(ValueError):
            projects.set_member(1, p["id"], "all", "viewer")
        with self.assertRaises(ValueError):
            projects.set_member(1, p["id"], "x:1", "viewer")

    def test_access_through_department(self) -> None:
        p = projects.create(1, "Орг", org_id="7")
        projects.set_member(1, p["id"], "g:3", "editor")
        with mock.patch(
            "assistant.stores.org_groups.principals",
            side_effect=lambda org, uid: {f"u:{uid}", "all", "g:3"} if str(uid) == "2" else {f"u:{uid}", "all"},
        ), mock.patch("assistant.stores.orgs_store.orgs_for_user", return_value=[{"id": 7}]):
            self.assertEqual(projects.role_of(2, projects.get(p["id"])), "editor")
            self.assertEqual(projects.role_of(4, projects.get(p["id"])), "")
            self.assertIn(p["id"], projects.ids_for_user(2))
            self.assertNotIn(p["id"], projects.ids_for_user(4))
            projects.set_member(1, p["id"], "all", "viewer")
            self.assertEqual(projects.role_of(4, projects.get(p["id"])), "viewer")

    def test_archived_hidden_from_list(self) -> None:
        p = projects.create(1, "Старый")
        projects.update(1, p["id"], {"archived": True})
        self.assertNotIn(p["id"], projects.ids_for_user(1))
        self.assertIn(p["id"], projects.ids_for_user(1, include_archived=True))

    def test_items_without_limit_and_access_filter(self) -> None:
        p = projects.create(1, "Большой")
        ids = [self._n(1, f"Д{i}") for i in range(bundles.MAX_ITEMS + 5)]
        projects.add_items(1, p["id"], [("note", i, "") for i in ids])
        self.assertEqual(len(projects.get(p["id"])["items"]), bundles.MAX_ITEMS + 5)
        projects.set_member(1, p["id"], "u:2", "viewer")
        v = bundle_access.visible_bundle(2, p["id"])
        self.assertEqual(len(v["items"]), bundles.MAX_ITEMS + 5)  # участнику открыто всё, что добавил автор
        self.assertEqual(v["hidden"], 0)
        with self.assertRaises(ValueError):
            projects.add_items(2, p["id"], [("note", ids[0], "")])  # смотрящий не добавляет

    def test_member_reads_project_docs_but_cannot_edit(self) -> None:
        n = self._n(1, "План")
        p = projects.create(1, "П", items=[("note", n, "")])
        self.assertIsNone(notes_store.get_readable_note(2, int(n)))
        projects.set_member(1, p["id"], "u:2", "viewer")
        r = notes_store.get_readable_note(2, int(n))
        assert r is not None
        self.assertFalse(r["can_edit"])
        self.assertFalse(r["can_comment"])  # смотрящий не комментирует
        self.assertIsNone(notes_store.get_accessible_note(2, int(n)))  # править нельзя
        projects.set_member(1, p["id"], "u:2", "editor")
        self.assertTrue(notes_store.get_readable_note(2, int(n))["can_comment"])
        projects.update(1, p["id"], {"archived": True})
        self.assertIsNone(notes_store.get_readable_note(2, int(n)))  # архив ничего не открывает

    def test_cannot_reshare_through_project_what_you_only_read(self) -> None:
        secret = self._n(1, "Чужой отчёт")
        note_members.add_member(1, int(secret), 3)  # 3 может править документ автора 1
        kb = self._n(1, "Только чтение")
        p = projects.create(3, "Проект 3")
        projects.add_items(3, p["id"], [("note", secret, "")])
        projects.set_member(3, p["id"], "u:4", "viewer")
        self.assertIsNotNone(notes_store.get_readable_note(4, int(secret)))  # 3 правит — может открыть
        with mock.patch("assistant.stores.notes.get_accessible_note", side_effect=lambda uid, nid, **kw: None):
            # тот, кто добавил, сам больше не может править — доступ через проект пропадает
            from assistant.services import project_access

            self.assertEqual(project_access.project_role_for_item(4, "note", secret), "")
        self.assertIsNone(notes_store.get_readable_note(4, int(kb)))

    def test_mail_and_tgchat_stay_personal_in_project(self) -> None:
        p = projects.create(1, "П")
        projects.set_member(1, p["id"], "u:2", "viewer")
        with mock.patch("assistant.stores.mail_index.thread",
                        side_effect=lambda uid, key: [{"subject": "Счёт", "from_name": "Бух"}] if str(uid) == "1" else []), \
             mock.patch("assistant.stores.chat_digest.is_human_member", side_effect=lambda cid, uid: int(uid) == 1), \
             mock.patch("assistant.stores.chat_digest.get_chat", return_value={"title": "Клиент"}):
            projects.add_items(1, p["id"], [("mail", "a1b2c3d4e5f60718", ""), ("tgchat", "-100", ""), ("series", "c:rec1", "Планёрка"),
                                            ("mailaddr", "Boss@Client.ru", "")])
            v1 = bundle_access.visible_bundle(1, p["id"])
            v2 = bundle_access.visible_bundle(2, p["id"])
        self.assertEqual({i["kind"] for i in v1["items"]}, {"mail", "tgchat", "series", "mailaddr"})
        self.assertEqual([i["kind"] for i in v2["items"]], ["series"])  # встречи видны участникам
        self.assertEqual(v2["hidden"], 3)
        self.assertEqual(bundles.norm_item("tgchat", "-100:7"), ("tgchat", "-100:7"))
        with self.assertRaises(ValueError):
            bundles.norm_item("mailaddr", "не адрес")

    def test_set_item_project_moves_between_editable_only(self) -> None:
        n = self._n(1, "Док")
        a = projects.create(1, "A")
        b = projects.create(1, "B")
        foreign = projects.create(2, "Чужой")
        projects.set_member(2, foreign["id"], "u:1", "viewer")
        note_members.add_member(1, int(n), 2)
        projects.add_items(2, foreign["id"], [("note", n, "")])
        projects.set_item_project(1, "note", n, a["id"])
        projects.set_item_project(1, "note", n, b["id"])
        self.assertEqual(set(projects.ids_with_item("note", n)), {b["id"], foreign["id"]})  # чужой не тронут
        projects.set_item_project(1, "note", n, None)
        self.assertEqual(projects.ids_with_item("note", n), [foreign["id"]])

    def test_related_still_needs_two_and_link_ignores_projects(self) -> None:
        a, b = self._n(1, "A"), self._n(1, "B")
        p = projects.create(1, "П", items=[("note", a, "")])
        rel = bundles.link(1, ("note", a, ""), ("note", b, ""))
        self.assertNotEqual(rel["id"], p["id"])
        self.assertEqual(rel["kind"], bundles.BUNDLE_RELATED)

    def test_named_bundles_become_projects_on_upgrade(self) -> None:
        a, b = self._n(1, "A"), self._n(1, "B")
        conn = bundles._conn()  # type: ignore[attr-defined]
        conn.execute("DELETE FROM bundle_members")
        conn.execute("INSERT INTO bundles(owner_user_id, title, created_at, updated_at, kind) VALUES ('1', 'Старая', 't', 't', 'related')")
        bid = int(conn.execute("SELECT max(id) FROM bundles").fetchone()[0])
        conn.execute("INSERT INTO bundle_items VALUES (?, 'note', ?, '', '1', 't')", (bid, a))
        conn.execute("INSERT INTO bundle_items VALUES (?, 'note', ?, '', '2', 't')", (bid, b))
        bundles._promote_named_bundles(conn)  # type: ignore[attr-defined]
        p = projects.get(bid)
        assert p is not None
        self.assertEqual(projects.role_of(1, p), "owner")
        self.assertEqual(projects.role_of(2, p), "editor")


class ProjectsApiTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        import usage_server

        self.us = usage_server

    def _p(self, uid: int):
        return self.us._MiniappPrincipal(uid, {"id": uid})

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_create_share_add_and_list(self) -> None:
        from fastapi import HTTPException

        us = self.us
        n = self._n(1, "Бриф")
        res = self._run(us.miniapp_project_create(
            us._MiniappProjectCreate(title="БГПТ", items=[us._MiniappLinkItem(kind="note", id=n)]), self._p(1)
        ))
        pid = res["project"]["id"]
        self.assertEqual(res["project"]["counts"], {"note": 1})
        self.assertEqual(res["project"]["members"][0]["role"], "owner")
        with self.assertRaises(HTTPException):
            self._run(us.miniapp_project_get(pid, self._p(2)))
        shared = self._run(us.miniapp_project_member_set(pid, us._MiniappProjectMember(principal="u:2", role="viewer"), self._p(1)))
        self.assertEqual({m["principal"] for m in shared["project"]["members"]}, {"u:1", "u:2"})
        listed = self._run(us.miniapp_projects_list(self._p(2)))
        self.assertEqual([p["title"] for p in listed["projects"]], ["БГПТ"])
        self.assertEqual(listed["projects"][0]["items"], [])  # список — без разбора элементов
        with self.assertRaises(HTTPException):
            self._run(us.miniapp_project_patch(pid, us._MiniappProjectPatch(title="Х"), self._p(2)))
        patched = self._run(us.miniapp_project_patch(pid, us._MiniappProjectPatch(status="done", lead_user_id="2"), self._p(1)))
        self.assertEqual(patched["project"]["status"], "done")
        self.assertEqual(patched["project"]["role"], "owner")
        with self.assertRaises(HTTPException):
            self._run(us.miniapp_project_create(us._MiniappProjectCreate(title="Орг", org_id="99"), self._p(1)))
        left = self._run(us.miniapp_project_member_remove(pid, "u:2", self._p(2)))
        # ушёл сам, но остался руководителем — видит как редактор
        self.assertEqual(left["project"]["role"], "editor")
        self.assertTrue(self._run(us.miniapp_project_delete(pid, self._p(1)))["ok"])


if __name__ == "__main__":
    unittest.main()
