"""Ссылки на Google Документы и Таблицы в сообщении чата: скачать и подложить модели текстом.

Модель сама по ссылкам не ходит и на «посмотри таблицу по ссылке» отвечала, что не умеет.
Открытые по ссылке документы скачиваем экспортом (документ — текст, таблица — CSV нужного листа)
и кладём в промпт так же, как текст вложенных файлов. Закрытые — подсказка, как открыть доступ.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Callable, Optional

MAX_LINKS = 3
MAX_CHARS = 60000

_LINK_RE = re.compile(
    r"https?://docs\.google\.com/(?:document|spreadsheets)/d/[a-zA-Z0-9_-]+[^\s<>\"')\]]*",
    re.IGNORECASE,
)


def find_links(text: str) -> list[str]:
    """Ссылки на Google Документы/Таблицы в тексте, без повторов, не больше MAX_LINKS."""
    out: list[str] = []
    for m in _LINK_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?»")
        if url not in out:
            out.append(url)
        if len(out) >= MAX_LINKS:
            break
    return out


def csv_to_text(raw: str, *, limit: int = MAX_CHARS) -> str:
    """CSV листа — строки со значениями через табуляцию (как текст .xlsx); пустые строки и хвосты убраны."""
    rows: list[str] = []
    total = 0
    count = 0
    shown = 0
    for row in csv.reader(io.StringIO(raw or "")):
        cells = [c.strip() for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        count += 1
        line = "\t".join(cells)
        if total + len(line) + 1 > limit:
            continue
        rows.append(line)
        total += len(line) + 1
        shown += 1
    text = "\n".join(rows)
    if shown < count:
        text += f"\n…(таблица обрезана: показано {shown} из {count} строк)"
    return text


def _label(url: str) -> str:
    return "Google Таблица" if "/spreadsheets/" in url.lower() else "Google Документ"


def prompt_notes(text: str, *, fetch: Optional[Callable[[str], object]] = None) -> str:
    """Блоки для промпта по каждой ссылке: содержимое или объяснение, почему его нет."""
    links = find_links(text)
    if not links:
        return ""
    from assistant.integrations import knowledge_google_docs as gdocs

    fetch = fetch or (lambda u: gdocs.fetch_google_doc(u, timeout=20))
    notes: list[str] = []
    for url in links:
        label = _label(url)
        try:
            doc = fetch(url)
        except gdocs.GoogleDocNoAccess:
            notes.append(
                f"{label} по ссылке {url} закрыта: доступа по ссылке нет, содержимое ты не видишь. "
                "Скажи об этом прямо и предложи открыть доступ «Все, у кого есть ссылка» (чтение) "
                "и прислать ссылку ещё раз — или выгрузить файл (.xlsx, .csv, .docx, PDF) и приложить. "
                "Не придумывай содержимое."
            )
            continue
        except Exception as e:  # сеть, таймаут, 5xx
            print(f"[chat_links] fetch failed url={url} err={e!r}")
            notes.append(
                f"{label} по ссылке {url} сейчас не скачалась (ошибка сети или Google). "
                "Скажи, что не получилось её открыть, и предложи повторить или приложить файл. "
                "Не придумывай содержимое."
            )
            continue
        body = str(getattr(doc, "text", "") or "")
        if "/spreadsheets/" in url.lower():
            body = csv_to_text(body)
        elif len(body) > MAX_CHARS:
            body = body[:MAX_CHARS] + f"\n…(документ обрезан: показано {MAX_CHARS} из {len(body)} символов)"
        notes.append(
            f"{label} по ссылке {url} (скачана для тебя, отвечай по её содержимому; "
            "не говори, что не умеешь открывать ссылки):\n" + body.strip()
        )
    return "\n\n".join(notes).strip()
