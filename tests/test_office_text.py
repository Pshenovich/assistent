import io
import zipfile
import unittest

from assistant.board.docs import extract_pptx, extract_xlsx

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _xlsx(rows_xml: str, *, extra_rows: int = 0) -> bytes:
    filler = "".join(
        f'<row r="{i}"><c r="A{i}" t="s"><v>0</v></c><c r="B{i}"><v>{i}</v></c></row>'
        for i in range(10, 10 + extra_rows)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook {_NS} {_R}><sheets><sheet name="Звонки" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            f"<sst {_NS}><si><t>Менеджер</t></si><si><t>Длительность</t></si>"
            f"<si><r><t>Анна </t></r><r><t>Ли</t></r></si></sst>",
        )
        zf.writestr(
            "xl/styles.xml",
            f'<styleSheet {_NS}><numFmts count="1"><numFmt numFmtId="164" formatCode="dd.mm.yyyy hh:mm"/>'
            f'<numFmt numFmtId="165" formatCode="0.0&quot; мин&quot;"/></numFmts>'
            f'<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/></cellXfs></styleSheet>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet {_NS}><sheetData>{rows_xml}{filler}</sheetData></worksheet>",
        )
    return buf.getvalue()


ROWS = (
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c><c r="D1" t="inlineStr"><is><t>Дата</t></is></c></row>'
    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2" s="2"><v>4.5</v></c><c r="D2" s="1"><v>46323.5</v></c></row>'
    '<row r="3"></row>'
)


class OfficeTextTests(unittest.TestCase):
    def test_xlsx_cells_dates_and_gaps(self) -> None:
        text = extract_xlsx(_xlsx(ROWS))
        lines = text.splitlines()
        self.assertEqual(lines[0], "Лист «Звонки»: 2 строк, 4 столбцов")
        self.assertEqual(lines[1], "Менеджер\t\tДлительность\tДата")
        # «0.0" мин"» — число, не дата; 46323.5 в стиле даты — дата со временем.
        self.assertEqual(lines[2], "Анна Ли\t\t4.5\t2026-10-28 12:00")

    def test_xlsx_truncation_is_announced(self) -> None:
        text = extract_xlsx(_xlsx(ROWS, extra_rows=500), limit=1500)
        self.assertLessEqual(len(text), 1500 + 200)  # +заголовок листа
        self.assertIn("502 строк", text)
        self.assertIn("остальные не влезли", text)
        # Сводка считается по всем строкам, а не по показанным.
        self.assertIn("- Менеджер: заполнено 501 из 501; значения: Менеджер — 500, Анна Ли — 1", text)
        self.assertIn("- столбец 2: заполнено 500 из 501; числа: сумма 129750", text)

    def test_pptx_slides_in_order(self) -> None:
        a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for n, body in ((10, "Итоги"), (2, "План")):
                zf.writestr(
                    f"ppt/slides/slide{n}.xml",
                    f"<sld {a}><a:p><a:r><a:t>{body}</a:t></a:r></a:p></sld>",
                )
        self.assertEqual(extract_pptx(buf.getvalue()), "Слайд 1:\nПлан\n\nСлайд 2:\nИтоги")

    def test_column_summary_kinds(self) -> None:
        from assistant.board.docs import _XlsxColumn

        dates = _XlsxColumn()
        for v in ("05.10.2026", "28.09.2026", "27.10.2026"):
            dates.add(v)
        self.assertIn("даты с 28.09.2026 по 27.10.2026", dates.describe(3))
        ids = _XlsxColumn()
        ids.add("1786669")
        self.assertTrue(ids.describe(1, "id звонка").endswith("идентификаторы"))
        self.assertIn("числа", ids.describe(1, "Видео"))
        notes = _XlsxColumn()
        for i in range(60):
            notes.add(f"рекомендация {i}")
        self.assertIn("свободный текст, 60 разных значений", notes.describe(60))


if __name__ == "__main__":
    unittest.main()
