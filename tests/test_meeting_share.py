"""«Поделиться с участниками»: доступ к встрече, свои задачи в Фокусе, общая отметка «поставлено»."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.services import focus_items as fi
from assistant.services import meeting_share as ms
from assistant.services import proactive
from assistant.services import proactive_ai as pa
from assistant.stores import meeting_links, note_members
from assistant.stores import notes as notes_store

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
TASKS = [
    {"assignee": "Ульяна", "task": "Собрать подтверждения от руководителей", "deadline": "сегодня"},
    {"assignee": "Антон", "task": "Передать API-ключ"},
    {"assignee": "", "task": "Подготовить план на ноябрь"},
    {"assignee": "Ульяна", "task": "Посчитать токены", "completed": True},
]


def note(**kw) -> dict:
    return {"id": 7, "title": "Командный синк", "created_at": (NOW - timedelta(hours=2)).isoformat(),
            "meta": {"tasks": [dict(t) for t in TASKS]}, **kw}


class SummaryTasksForMembersTests(unittest.TestCase):
    def test_same_card_for_everyone_closed_shown_with_author(self) -> None:
        n = note()
        n["meta"]["tasks"][3].update(focus_status="converted", completed_by="Ульяна", link={"task_id": 42})
        own = proactive.rule_summary_tasks([n], {}, NOW, timezone.utc)[0]
        shared = proactive.rule_summary_tasks([n], {}, NOW, timezone.utc, foreign={7})[0]
        self.assertEqual([i["text"] for i in own["items"]], [i["text"] for i in shared["items"]])
        self.assertIn("3 задачи", own["title"])  # закрытая в счёт не идёт
        self.assertEqual(own["closed_items"], [{"id": own["closed_items"][0]["id"], "text": "Ульяна — Посчитать токены",
                                                "status": "converted", "by": "Ульяна", "link": {"task_id": 42}}])

    def test_owner_mine_flag_not_applied_to_member(self) -> None:
        n = note()
        n["meta"]["tasks"][1]["owner_flag"] = "mine"
        own = proactive.rule_summary_tasks([n], {}, NOW, timezone.utc)[0]
        shared = proactive.rule_summary_tasks([n], {}, NOW, timezone.utc, foreign={7})[0]
        self.assertTrue(any(i.get("mine") for i in own["items"]))
        self.assertFalse(any(i.get("mine") for i in shared["items"]))

    def test_task_posted_by_member_closed_with_author(self) -> None:
        posted = [{"title": "Собрать подтверждения от руководителей", "by": "Ульяна", "task_id": 9}]
        out = proactive.rule_summary_tasks([note()], {7: posted}, NOW, timezone.utc)[0]
        self.assertNotIn("Собрать подтверждения", " ".join(i["text"] for i in out["items"]))
        self.assertIn({"status": "converted", "by": "Ульяна", "link": {"task_id": 9}},
                      [{k: c[k] for k in ("status", "by", "link")} for c in out["closed_items"]])

    def test_all_closed_no_card(self) -> None:
        n = note()
        for t in n["meta"]["tasks"]:
            t["completed"] = True
        self.assertEqual(proactive.rule_summary_tasks([n], {}, NOW, timezone.utc), [])


class ShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        self._reset()
        self.note = notes_store.create_note(1, "Командный синк", "<p>саммари</p>", meta={"tasks": [dict(t) for t in TASKS]})
        self.nid = int(self.note["id"])
        meeting_links.link(1, self.nid, {
            "id": "ev1", "summary": "Командный синк",
            "start": {"dateTime": "2026-10-22T10:00:00+03:00"}, "end": {"dateTime": "2026-10-22T11:00:00+03:00"},
            "attendees": [{"email": "me@x.ru", "name": "Вова"}, {"email": "u@x.ru", "name": "Ульяна Виноградова"},
                          {"email": "a@x.ru", "name": "Андрей"}],
        }, source="upload")

    def tearDown(self) -> None:
        self._reset()
        self._tmp.cleanup()

    def _reset(self) -> None:
        for m in (notes_store, pa, proactive, fi, meeting_links, note_members):
            m._CONN = None  # type: ignore[attr-defined]

    def _share(self) -> dict:
        ids = {"me@x.ru": 1, "u@x.ru": 2}
        with mock.patch("assistant.services.meeting_invites.telegram_id_for_attendee_email",
                        side_effect=lambda owner, em, **kw: ids.get(em)), \
                mock.patch("assistant.bot.access_gate.is_user_allowed", return_value=True):
            return ms.share_with_attendees(1, self.nid)

    def test_share_adds_leo_users_and_reports_missing(self) -> None:
        res = self._share()
        self.assertEqual([m["user_id"] for m in res["added"]], [2])
        self.assertEqual([m["name"] for m in res["missing"]], ["Андрей"])
        self.assertTrue(note_members.is_member(1, self.nid, 2))
        self.assertEqual(self._share()["already"], [{"user_id": 2, "name": "Ульяна Виноградова"}])

    def test_member_marking_task_updates_owner_note(self) -> None:
        self._share()
        fi.remember(2, {
            "key": "k1", "card_key": "k1", "kind": "task", "text": "Собрать подтверждения от руководителей", "who": "Ульяна",
            "source": {"kind": "note", "id": self.nid, "title": "Командный синк"},
        })
        fi.apply(2, "k1", "done", by="Ульяна")
        t = notes_store.get_note(1, self.nid)["meta"]["tasks"][0]
        self.assertTrue(t["completed"])
        self.assertEqual(t["completed_by"], "Ульяна")
        # у владельца задача закрыта, в чате видно, кто её закрыл
        card = proactive.rule_summary_tasks([notes_store.get_note(1, self.nid)], {}, NOW, timezone.utc)[0]
        self.assertNotIn("Собрать подтверждения", " ".join(i["text"] for i in card["items"]))
        snaps = fi.item_from_card(card, with_closed=True)
        self.assertEqual(len(snaps), 4)
        self.assertEqual(len(fi.item_from_card(card)), 2)  # в реестр обязательств закрытые не идут
        for sn in snaps:
            fi.remember(1, sn)
        shown = ms.overlay(1, [fi.public(fi.get(1, sn["key"])) for sn in snaps])
        done = [f for f in shown if f["status"] != "new"]
        self.assertEqual([(f["text"], f["status"], f["by"]) for f in done], [
            ("Собрать подтверждения от руководителей", "completed", "Ульяна"), ("Посчитать токены", "completed", "")])

    def test_member_edit_visible_to_owner(self) -> None:
        self._share()
        card = proactive.rule_summary_tasks([notes_store.get_note(1, self.nid)], {}, NOW, timezone.utc)[0]
        for sn in fi.item_from_card(card, with_closed=True):
            fi.remember(1, sn)
            fi.remember(2, sn)
        key = card["items"][1]["id"]  # «Антон — Передать API-ключ»
        fi.apply(2, key, "update", fields={"assignee": "Артём", "deadline": "пятница"})
        shown = {f["key"]: f for f in ms.overlay(1, [fi.public(fi.get(1, key))])}
        self.assertEqual((shown[key]["assignee"], shown[key]["deadline"], shown[key]["status"]), ("Артём", "пятница", "new"))

    def test_not_a_task_shared_not_me_personal(self) -> None:
        self._share()
        card = proactive.rule_summary_tasks([notes_store.get_note(1, self.nid)], {}, NOW, timezone.utc)[0]
        for sn in fi.item_from_card(card, with_closed=True):
            fi.remember(2, sn)
        k_dup, k_mine = card["items"][0]["id"], card["items"][1]["id"]
        fi.apply(2, k_dup, "dismiss", reason="duplicate", by="Ульяна")
        fi.apply(2, k_mine, "dismiss", reason="not_me", by="Ульяна")
        card = proactive.rule_summary_tasks([notes_store.get_note(1, self.nid)], {}, NOW, timezone.utc)[0]
        open_ids = [i["id"] for i in card["items"]]
        self.assertNotIn(k_dup, open_ids)  # «Дубль» — у всех
        self.assertIn(k_mine, open_ids)  # «Не моя» — только у того, кто отказался
        tasks = notes_store.get_note(1, self.nid)["meta"]["tasks"]
        self.assertEqual(tasks[0].get("focus_reason"), "duplicate")
        self.assertNotIn("focus_status", tasks[1])

    def test_posted_task_done_marks_summary_completed(self) -> None:
        self._share()
        card = proactive.rule_summary_tasks([notes_store.get_note(1, self.nid)], {}, NOW, timezone.utc)[0]
        for sn in fi.item_from_card(card, with_closed=True):
            fi.remember(1, sn)
        key = card["items"][0]["id"]
        fi.apply(1, key, "converted", task_id=55, by="Вова")
        self.assertEqual(fi.task_done_changed(1, 55, True, "Ульяна"), 1)
        note_now = notes_store.get_note(1, self.nid)
        t = note_now["meta"]["tasks"][0]
        self.assertEqual((t["focus_status"], t["completed_by"]), ("completed", "Ульяна"))
        self.assertIn('data-leo-mark-done="1"', note_now["body"])
        self.assertEqual(ms.task_state(t, [])["status"], "completed")
        self.assertEqual(fi.task_done_changed(1, 55, False), 1)
        self.assertEqual(notes_store.get_note(1, self.nid)["meta"]["tasks"][0]["focus_status"], "converted")


if __name__ == "__main__":
    unittest.main()
