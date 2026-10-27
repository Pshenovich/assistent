"""«Фокус», этап 2: ИИ по событиям — что разбирать, разбор, карточки; утренний бриф."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from assistant.services import proactive, proactive_ai as pa
from assistant.stores import notes as notes_store

TODAY = date(2026, 10, 17)
NOTE = {"id": 5, "kind": "note", "title": "1:1"}


class CardsTests(unittest.TestCase):
    def test_normalize_drops_garbage(self) -> None:
        items = pa.normalize_items({"items": [
            {"kind": "agreement", "text": "Релиз", "who": "Андрей", "due": "2026-10-18"},
            {"kind": "weird", "text": "x"},
            {"kind": "open_question", "text": ""},
            {"kind": "followup", "text": "Созвон", "due": "в пятницу"},
        ]})
        self.assertEqual([i["kind"] for i in items], ["agreement", "followup"])
        self.assertEqual(items[1]["due"], "")
        self.assertEqual(pa.normalize_items("nope"), [])

    def test_agreement_card_and_covered_by_task(self) -> None:
        it = {"kind": "agreement", "text": "Андрей выкатит правки в тренды", "who": "Андрей", "due": "2026-10-17"}
        c = pa.card_for(it, src="из «1:1»", note=NOTE, today=TODAY, task_titles=[])
        self.assertEqual(c["title"], "Андрей — выкатит правки в тренды")
        self.assertEqual(c["urgency"], 3)
        self.assertIn("исполнителю Андрей", c["actions"][0]["payload"]["prompt"])
        self.assertEqual(c["actions"][-1]["act"], "mark_done")
        self.assertEqual(c["items"], [{"id": c["key"], "text": it["text"]}])
        self.assertIsNone(pa.card_for(it, src="", note=NOTE, today=TODAY, task_titles=["Выкатить правки в тренды"]))
        old = dict(it, due="2026-09-01")
        self.assertIsNone(pa.card_for(old, src="", note=NOTE, today=TODAY, task_titles=[]))

    def test_me_and_followup_and_question(self) -> None:
        me = pa.card_for({"kind": "agreement", "text": "Прислать смету", "who": "я", "due": ""}, src="s", note=NOTE, today=TODAY, task_titles=[])
        self.assertTrue(me["title"].startswith("Вы обещали"))
        self.assertEqual(me["actions"][0]["label"], "Себе")
        fu = pa.card_for({"kind": "followup", "text": "Созвон по бюджету", "people": ["Мария"]}, src="s", note=NOTE, today=TODAY, task_titles=[])
        self.assertIn("с Мария", fu["source"])
        self.assertIn("3 варианта", fu["actions"][0]["payload"]["prompt"])
        q = pa.card_for({"kind": "open_question", "text": "Какой анонимизатор"}, src="s", note=NOTE, today=TODAY, task_titles=[])
        self.assertEqual(q["title"], "Не решено: Какой анонимизатор")
        self.assertNotIn("mark_done", [a["act"] for a in fu["actions"] + q["actions"]])

    def test_promise_only_from_my_message(self) -> None:
        # Чужое «завтра поставлю в прод» — не «вы обещали»: автор сверяется по номеру сообщения.
        authors = {10: 777, 11: 555}
        self.assertTrue(pa.promise_is_mine(777, {"kind": "promise", "msg": 10}, authors))
        self.assertFalse(pa.promise_is_mine(777, {"kind": "promise", "msg": 11}, authors))
        self.assertFalse(pa.promise_is_mine(777, {"kind": "promise", "msg": 0}, authors))
        self.assertFalse(pa.promise_is_mine(777, {"kind": "promise", "msg": 99}, authors))

    def test_chat_card_mark_done_only_for_tasks(self) -> None:
        promise = pa.chat_card({"kind": "promise", "text": "Пришлю доступы к стенду", "msg": 3}, chat_id=-100, day="2026-10-17",
                               title="Бустра", today=TODAY, task_titles=[])
        self.assertEqual(promise["actions"][-1]["act"], "mark_done")
        self.assertEqual(promise["items"][0]["id"], promise["key"])
        esc = pa.chat_card({"kind": "escalation", "text": "Клиент недоволен сроками", "who": "Иван", "msg": 4}, chat_id=-100,
                           day="2026-10-17", title="Бустра", today=TODAY, task_titles=[])
        self.assertNotIn("mark_done", [a["act"] for a in esc["actions"]])
        self.assertIsNone(pa.chat_card({"kind": "promise", "text": "Пришлю доступы к стенду", "msg": 3}, chat_id=-100,
                                       day="2026-10-17", title="Бустра", today=TODAY, task_titles=["Прислать доступы к стенду"]))


class TriggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        for m in (notes_store, pa, proactive):
            m._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        for m in (notes_store, pa, proactive):
            m._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_only_changed_and_idle_notes_analyzed_once(self) -> None:
        body = "<p>" + "Андрей сделает релиз до пятницы. " * 10 + "</p>"
        n = notes_store.create_note(1, "1:1", body)
        notes_store.create_note(1, "short", "<p>мало</p>")
        later = datetime.now(timezone.utc) + timedelta(minutes=11)
        self.assertEqual(pa.candidates(1), [])  # ещё не «остыла» (правка только что)
        cands = pa.candidates(1, now=later)
        self.assertEqual([c[0]["id"] for c in cands], [n["id"]])
        fake = [{"kind": "agreement", "text": "Андрей сделает релиз", "who": "Андрей", "due": ""}]
        with mock.patch.object(pa, "_llm_items", return_value=fake), mock.patch(
            "assistant.integrations.openrouter_client.set_openrouter_usage_telegram_user"
        ):
            pa.analyze_note(1, cands[0][0], cands[0][1], cands[0][2])
        self.assertEqual(pa.candidates(1, now=later), [])  # тот же текст повторно не разбираем
        self.assertEqual(pa.analyzed_today(1), 1)
        cards = pa.insight_cards(1, task_titles=[])
        self.assertEqual(cards[0]["type"], "agreement")
        proactive.mark_done(1, cards[0]["key"], cards[0]["items"], whole=True)
        self.assertEqual(pa.insight_cards(1, task_titles=[]), [])  # «Уже есть» — похожее больше не предлагаем
        notes_store.update_note(1, n["id"], body=body + "<p>Мария пришлёт смету.</p>")
        self.assertEqual(len(pa.candidates(1, now=datetime.now(timezone.utc) + timedelta(minutes=11))), 1)

    def test_ready_note_skips_idle_wait(self) -> None:
        body = "<p>" + "Итоги: Мария пришлёт смету до среды. " * 10 + "</p>"
        n = notes_store.create_note(1, "Итоги встречи", body)
        self.assertEqual(pa.candidates(1), [])  # обычная правка — ждём тишины
        self.assertEqual([c[0]["id"] for c in pa.candidates(1, ready_ids={int(n["id"])})], [n["id"]])

    def test_note_changed_debounces_and_respects_autostart(self) -> None:
        with mock.patch.dict(os.environ, {"PROACTIVE_AI_AUTOSTART": "0"}):
            pa.note_changed(1, 77)
            self.assertNotIn((1, 77), pa._TIMERS)
        with mock.patch.dict(os.environ, {"PROACTIVE_AI_AUTOSTART": "1"}), mock.patch.object(pa, "kick") as kick:
            pa.note_changed(1, 77)
            first = pa._TIMERS[(1, 77)]
            pa.note_changed(1, 77)  # повторная правка переносит разбор
            self.assertFalse(first.is_alive() and pa._TIMERS[(1, 77)] is first)
            pa.note_changed(1, 77, ready=True)  # саммари готово — разбор сразу
            self.assertTrue(pa.busy(1))
            pa.note_changed(1, 77)  # правка не откладывает немедленный разбор
            self.assertTrue(getattr(pa._TIMERS[(1, 77)], "_leo_ready", False))
            pa._TIMERS[(1, 77)].join(3)
            kick.assert_called_once_with(1, {77})
        self.assertNotIn((1, 77), pa._TIMERS)

    def test_kick_marks_busy_and_records_last_analyzed(self) -> None:
        with mock.patch.object(pa, "candidates", return_value=[({"id": 9}, "t", "h")]), mock.patch.object(
            pa, "analyze_note"
        ), mock.patch.object(pa, "digest_candidates", return_value=[]):
            before = pa.last_analyzed_at(5)
            self.assertEqual(pa.run_pending(5), 1)
            self.assertGreater(pa.last_analyzed_at(5), before)
        self.assertFalse(pa.busy(5))

    def test_analyze_now_takes_fresh_notes_and_reports(self) -> None:
        body = "<p>" + "Итоги: Мария пришлёт смету до среды. " * 10 + "</p>"
        n = notes_store.create_note(1, "Итоги встречи", body)
        with mock.patch.object(pa, "kick") as kick, mock.patch.object(pa, "digest_candidates", return_value=[]):
            res = pa.analyze_now(1)
        self.assertEqual(res, {"queued": 1, "pending": 1, "capped": False})
        kick.assert_called_once_with(1, {int(n["id"])}, limit=pa.PER_RUN_MANUAL)
        with mock.patch.object(pa, "kick") as kick, mock.patch.object(pa, "digest_candidates", return_value=[]), \
                mock.patch.object(pa, "analyzed_today", return_value=pa.DAILY_CAP):
            self.assertEqual(pa.analyze_now(1), {"queued": 0, "pending": 1, "capped": True})
            kick.assert_not_called()

    def test_analyze_now_nothing_new(self) -> None:
        with mock.patch.object(pa, "kick") as kick, mock.patch.object(pa, "digest_candidates", return_value=[]):
            self.assertEqual(pa.analyze_now(1), {"queued": 0, "pending": 0, "capped": False})
            kick.assert_not_called()


class BriefTests(unittest.TestCase):
    def test_brief_due_window(self) -> None:
        base = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
        self.assertFalse(proactive.brief_due(base, "08:30"))
        self.assertTrue(proactive.brief_due(base.replace(hour=8, minute=31), "08:30"))
        self.assertFalse(proactive.brief_due(base.replace(hour=12), "08:30"))

    def test_brief_message(self) -> None:
        now = datetime(2026, 10, 17, 8, 30, tzinfo=timezone.utc)
        evs = [{"summary": "1:1 с Андреем", "start": {"dateTime": "2026-10-17T11:00:00+00:00"}}]
        text = proactive.brief_message([{"title": "Смета <для> Ромашки"}], evs, timezone.utc, now)
        self.assertIn("Суббота, 17 окт", text)
        self.assertIn("1 встреча", text)
        self.assertIn("&lt;для&gt;", text)


if __name__ == "__main__":
    unittest.main()
