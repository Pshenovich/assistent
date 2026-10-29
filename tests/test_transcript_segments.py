import os
import tempfile
import unittest

from assistant.integrations import transcribe as obu
from assistant.lib import transcript_render as tr
from assistant.services import transcription_notes as tnotes
from assistant.stores import note_sheets as sheets
from assistant.stores import notes as notes_store
from assistant.stores import transcript_segments

# Ответ Obuchat как сейчас: сегменты в «transcript», реплики перекрываются по времени.
OBUCHAT_BODY = {
    "job_id": "j1",
    "status": "done",
    "transcript": [
        {"index": 0, "speaker": "SPEAKER_1", "start": 0.0, "end": 5.0, "text": "Дима, подготовишь отчёт к пятнице?"},
        {"index": 2, "speaker": "SPEAKER_1", "start": 7.6, "end": 16.1, "text": "И бюджет с финансами."},
        {"index": 1, "speaker": "SPEAKER_2", "start": 7.0, "end": 7.5, "text": "Да, сделаю."},
        {"index": 3, "speaker": "SPEAKER_1", "start": 20.0, "end": 24.0, "text": "Отлично."},
    ],
    "text": "SPEAKER_1: Дима, подготовишь отчёт к пятнице?\nSPEAKER_2: Да, сделаю.",
}


def _seg(sp, start, end, text):
    return {"speaker": sp, "start": start, "end": end, "text": text}


class ParseTests(unittest.TestCase):
    def test_segments_from_transcript_list(self) -> None:
        r = obu._result_from_body(OBUCHAT_BODY, job_id="j1")
        self.assertEqual([s["speaker"] for s in r.segments], ["Спикер 1", "Спикер 2", "Спикер 1", "Спикер 1"])
        self.assertEqual(r.segments[1]["start"], 7.0)  # отсортировано по времени
        self.assertTrue(r.formatted_text.startswith("Спикер 1:\nДима"))
        self.assertIn("Спикер 1:\nИ бюджет с финансами. Отлично.", r.formatted_text)
        self.assertNotIn("SPEAKER", r.plain_text)

    def test_single_speaker_has_no_labels(self) -> None:
        body = {"transcript": [{"speaker": "SPEAKER_0", "start": 0, "end": 1, "text": "Заметка себе."}]}
        r = obu._result_from_body(body)
        self.assertEqual(r.segments[0]["speaker"], "")
        self.assertEqual(r.formatted_text, "Заметка себе.")

    def test_legacy_text_only(self) -> None:
        r = obu._result_from_body({"text": "SPEAKER_1: Привет\nSPEAKER_2: Пока"})
        self.assertEqual(r.segments, ())
        self.assertIn("Привет", r.plain_text)


class RenderTests(unittest.TestCase):
    def test_paragraph_on_pause_and_speaker(self) -> None:
        segs = [
            _seg("Спикер 1", 0, 3, "Первая мысль."),
            _seg("Спикер 1", 3.5, 6, "Продолжение."),
            _seg("Спикер 1", 10, 12, "После паузы."),
            _seg("Спикер 2", 65, 70, "Ответ."),
        ]
        html = tr.render_html(segs)
        self.assertEqual(html.count("<p>"), 3)
        self.assertIn("<strong>Спикер 1</strong> · 0:00<br>\nПервая мысль. Продолжение.", html)
        self.assertIn("<p>После паузы.</p>", html)
        self.assertIn("<strong>Спикер 2</strong> · 1:05", html)

    def test_only_confirmed_names_replace_labels(self) -> None:
        segs = [_seg("Спикер 1", 0, 1, "А"), _seg("Спикер 2", 2, 3, "Б")]
        smap = {
            "Спикер 1": {"name": "Петя", "confirmed": True},
            "Спикер 2": {"name": "Дима", "confidence": 0.8},  # догадка
        }
        html = tr.render_html(segs, smap)
        self.assertIn("<strong>Петя</strong>", html)
        self.assertIn("<strong>Спикер 2</strong>", html)

    def test_long_monologue_split(self) -> None:
        segs = [_seg("Спикер 1", i * 10, i * 10 + 9, f"кусок {i}.") for i in range(8)]
        self.assertEqual(len(tr.turns(segs)[0]["paras"]), 2)

    def test_fmt_time(self) -> None:
        self.assertEqual(tr.fmt_time(3725), "1:02:05")
        self.assertEqual(tr.fmt_time(None), "")


class NoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self.segs = list(obu._result_from_body(OBUCHAT_BODY).segments)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def _note(self):
        return tnotes.create_transcript_only(
            1, title="Созвон", transcript="плоский текст", segments=self.segs
        )

    def test_transcript_only_renders_segments(self) -> None:
        note = self._note()
        self.assertIn("<strong>Спикер 2</strong>", note["body"])
        self.assertTrue(note["meta"]["speakers_detected"])
        self.assertEqual(len(transcript_segments.get(int(note["id"]))), 4)

    def test_confirm_speaker_without_summary(self) -> None:
        note = self._note()
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertIn("<strong>Дима</strong>", fresh["body"])
        self.assertTrue(fresh["meta"]["speaker_map"]["Спикер 2"]["confirmed"])
        self.assertIn("Дима:\nДа, сделаю.", tnotes.transcript_for_llm(fresh))

    def test_confirm_speaker_keeps_hand_edited_transcript(self) -> None:
        note = self._note()
        notes_store.update_note(1, int(note["id"]), body="<p>Исправил руками</p>")
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertEqual(fresh["body"], "<p>Исправил руками</p>")
        self.assertEqual(tnotes.transcript_for_llm(fresh), "Исправил руками")

    def test_confirm_speaker_after_editor_resave(self) -> None:
        # Редактор пересохраняет лист: <br> без перевода строки, абзацы подряд, id строк.
        note = self._note()
        resaved = note["body"].replace("<br>\n", "<br>").replace("</p>\n<p>", '</p><p data-leo-item="x">')
        notes_store.update_note(1, int(note["id"]), body=resaved)
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertIn("<strong>Дима</strong>", fresh["body"])

    def test_confirm_speaker_after_stale_overwrite(self) -> None:
        # Вкладка досохранила старый транскрипт со «Спикер 2» поверх «Димы» — это не ручная правка.
        note = self._note()
        stale = note["body"]
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        notes_store.update_note(1, int(note["id"]), body=stale)
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 1", "Петя")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertIn("<strong>Дима</strong>", fresh["body"])
        self.assertIn("<strong>Петя</strong>", fresh["body"])

    def test_confirm_unknown_speaker(self) -> None:
        note = self._note()
        with self.assertRaises(tnotes.TranscriptionNoteError):
            tnotes.confirm_speaker(1, int(note["id"]), "Спикер 9", "Кто-то")

    def test_rename_after_summary_updates_transcript_sheet(self) -> None:
        note = tnotes.create_with_summary(
            1, title="W", transcript="x", summary="Итог", segments=self.segs
        )
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 1", "Петя")
        extra = sheets.list_extra_sheets(int(note["id"]))[0]
        self.assertIn("<strong>Петя</strong>", extra["body"])

    def test_merge_speakers(self) -> None:
        note = self._note()
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        tnotes.merge_speakers(1, int(note["id"]), "Спикер 2", "Спикер 1")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertEqual({s["speaker"] for s in transcript_segments.get(int(note["id"]))}, {"Спикер 1"})
        self.assertEqual(fresh["meta"]["speaker_map"]["Спикер 1"]["name"], "Дима")
        self.assertNotIn("Спикер 2", fresh["body"])

    def test_speakers_view(self) -> None:
        note = self._note()
        view = tnotes.speakers_view(1, int(note["id"]))
        self.assertEqual([x["label"] for x in view["speakers"]], ["Спикер 1", "Спикер 2"])
        first = view["speakers"][0]
        self.assertEqual(first["talk_sec"], 18)
        self.assertEqual(first["samples"][0]["t"], "0:00")
        self.assertTrue(view["can_merge"])

    def _v1_note(self):
        tasks = [
            {"id": "i0000000001", "assignee": "Спикер 2", "task": "Подготовить отчёт", "deadline": "пятница"},
            {"id": "i0000000002", "assignee": "Аня", "task": "Прислать цифры", "deadline": ""},
        ]
        summary = "## Итог\nСпикер 2 обещал отчёт, Спикер 20 молчал.\n\n## Задачи\n- Спикер 2 — Подготовить отчёт — пятница\n- Аня — Прислать цифры"
        return tnotes.create_with_summary(1, title="W", transcript="x", summary=summary, meta={"tasks": tasks}, segments=self.segs)

    def test_rename_in_v1_summary_and_tasks(self) -> None:
        note = self._v1_note()
        out = tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дима")
        self.assertTrue(out["summary_renamed"])
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertIn("Дима обещал отчёт", fresh["body"])
        self.assertIn("Спикер 20 молчал", fresh["body"])
        self.assertNotIn("Спикер 2 ", fresh["body"])
        self.assertEqual(fresh["meta"]["tasks"][0]["assignee"], "Дима")
        self.assertEqual(fresh["meta"]["tasks"][0]["id"], "i0000000001")
        # переименование: прежнее имя меняется в задаче, но не в остальном тексте
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Дмитрий")
        fresh = notes_store.get_note(1, int(note["id"]))
        self.assertEqual(fresh["meta"]["tasks"][0]["assignee"], "Дмитрий")
        self.assertIn("Дима обещал отчёт", fresh["body"])
        # задачи — таблица: новое имя в ячейке «Ответственный» строки задачи
        self.assertIn('<p>Подготовить отчёт</p></td><td><p>Не разобрано</p></td><td><p>Дмитрий</p></td>', fresh["body"])

    def test_is_me_and_dialog(self) -> None:
        note = self._note()
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 1", "Вова", is_me=True)
        dlg = tnotes.transcript_dialog(1, int(note["id"]))
        self.assertTrue(dlg["has_me"])
        self.assertEqual([(t["name"], t["me"]) for t in dlg["turns"]][:2], [("Вова", True), ("Спикер 2", False)])
        self.assertTrue(tnotes.speakers_view(1, int(note["id"]))["speakers"][0]["is_me"])
        tnotes.confirm_speaker(1, int(note["id"]), "Спикер 2", "Я же", is_me=True)
        smap = notes_store.get_note(1, int(note["id"]))["meta"]["speaker_map"]
        self.assertNotIn("user_id", smap["Спикер 1"])
        self.assertEqual(smap["Спикер 2"]["user_id"], "1")

    def test_delete_note_drops_segments(self) -> None:
        note = self._note()
        notes_store.delete_note(1, int(note["id"]))
        self.assertIsNone(transcript_segments.get(int(note["id"])))


class GuessTests(unittest.TestCase):
    SEGS = [
        {"speaker": "Спикер 1", "start": 0, "end": 2, "text": "Это Петя из продаж. Дима, что скажешь?"},
        {"speaker": "Спикер 2", "start": 3, "end": 5, "text": "Сделаю отчёт."},
        {"speaker": "Спикер 3", "start": 6, "end": 7, "text": "Угу."},
    ]

    def _guess(self, reply):
        from assistant.nlu import speaker_guess

        people = [{"name": "Дмитрий Иванов", "aliases": ["Дима"], "email": ""}]
        return speaker_guess.guess(
            self.SEGS, context_payload={}, people=people, chat=lambda *a, **k: reply, model="m"
        )

    def test_names_matched_to_contacts(self) -> None:
        out = self._guess(
            '{"speaker_map":{"Спикер 1":{"name":"Петя","confidence":0.95,"evidence":"это Петя"},'
            '"Спикер 2":{"name":"Дима","confidence":0.8,"evidence":"ответ на обращение"},'
            '"Спикер 7":{"name":"Вася","confidence":0.9}}}'
        )
        self.assertEqual(out["Спикер 1"]["name"], "Петя")
        self.assertEqual(out["Спикер 2"]["name"], "Дмитрий Иванов")
        self.assertFalse(out["Спикер 2"]["confirmed"])
        self.assertNotIn("Спикер 7", out)

    def test_one_name_one_speaker(self) -> None:
        out = self._guess(
            '{"speaker_map":{"Спикер 2":{"name":"Петя","confidence":0.6},'
            '"Спикер 1":{"name":"Петя","confidence":0.9}}}'
        )
        self.assertEqual(list(out), ["Спикер 1"])

    def test_bad_json(self) -> None:
        self.assertEqual(self._guess("не json"), {})


if __name__ == "__main__":
    unittest.main()
