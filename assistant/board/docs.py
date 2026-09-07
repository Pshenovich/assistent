"""Извлечение текста из документов компании (без новых зависимостей)."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_DOC_BYTES = 2 * 1024 * 1024
MAX_DOC_CHARS = 50_000
ALLOWED_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".docx"}

_PRODUCT_HINTS = (
    "продукт",
    "product",
    "каталог",
    "sku",
    "прайс",
    "оферт",
    "тариф",
    "price",
)
_TEAM_HINTS = (
    "сотрудник",
    "команд",
    "staff",
    "team",
    "people",
    "оргструктур",
    "org chart",
)


def detect_doc_kind(filename: str, caption: str = "") -> str:
    blob = f"{filename} {caption}".lower()
    if any(k in blob for k in _PRODUCT_HINTS):
        return "products"
    if any(k in blob for k in _TEAM_HINTS):
        return "team"
    return "general"


def kind_label(kind: str) -> str:
    return {
        "products": "продукты",
        "team": "команда",
        "live": "живой документ",
    }.get(kind, "общий")


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    w_p = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    w_t = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    paras: list[str] = []
    for p in root.iter(w_p):
        line = "".join((t.text or "") for t in p.iter(w_t)).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


def extract_document_text(
    filename: str,
    data: bytes,
    mime: str = "",
) -> tuple[str, str | None]:
    if not data:
        return "", "Файл пустой."
    if len(data) > MAX_DOC_BYTES:
        return "", "Файл слишком большой (макс. 2 МБ). Пришлите .txt/.md/.csv/.docx."
    ext = Path(filename or "").suffix.lower()
    if not ext and mime:
        if "word" in mime:
            ext = ".docx"
        elif mime.startswith("text/") or mime.endswith("json"):
            ext = ".txt"
    if ext == ".pdf":
        return "", "PDF пока не поддерживается — пришлите .txt, .md, .csv или .docx."
    if ext not in ALLOWED_EXT:
        return (
            "",
            f"Формат {ext or mime or 'файла'} не поддерживается. "
            "Пришлите .txt, .md, .csv или .docx.",
        )
    try:
        if ext == ".docx":
            text = extract_docx(data)
        else:
            text = decode_text(data)
    except Exception:
        return "", "Не удалось прочитать файл. Проверьте, что это текст или Word (.docx)."
    text = text.strip()
    if not text:
        return "", "В файле нет текста."
    if len(text) > MAX_DOC_CHARS:
        text = text[: MAX_DOC_CHARS - 1] + "…"
    return text, None
