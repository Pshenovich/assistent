"""Ссылки на Google Документы и Таблицы в сообщении чата: скачать и подложить модели текстом.

Модель сама по ссылкам не ходит и на «посмотри таблицу по ссылке» отвечала, что не умеет.
Открытые по ссылке документы скачиваем экспортом (документ — текстом, таблица — .xlsx со всеми
листами) и кладём в промпт так же, как текст вложенных файлов. Закрытые — подсказка, как открыть
доступ. Если всё вместе не влезает в один запрос (INLINE_LIMIT), чат обрабатывает данные
частями в фоне (assistant/services/bulk_links.py), а модели достаются итоги частей.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

MAX_LINKS = 3
# Столько символов данных по ссылкам кладём в один запрос; больше — обработка частями.
INLINE_LIMIT = 150_000
CACHE_TTL_SEC = 600.0

_LINK_RE = re.compile(
    r"https?://docs\.google\.com/(?:document|spreadsheets)/d/[a-zA-Z0-9_-]+[^\s<>\"')\]]*",
    re.IGNORECASE,
)


@dataclass
class Source:
    url: str
    label: str  # «Google Таблица» / «Google Документ»
    error: str = ""  # "no_access" | "failed" | ""
    sheets: list[tuple[str, list[str]]] = field(default_factory=list)  # таблица: (лист, строки)
    text: str = ""  # документ
    xlsx: bytes = b""

    @property
    def is_sheet(self) -> bool:
        return "/spreadsheets/" in self.url.lower()

    @property
    def size(self) -> int:
        if self.error:
            return 0
        if self.is_sheet:
            return sum(len(x) + 1 for _t, rows in self.sheets for x in rows)
        return len(self.text)


_CACHE: dict[str, tuple[float, Source]] = {}
_CACHE_LOCK = threading.Lock()


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


def csv_to_text(raw: str, *, limit: int = INLINE_LIMIT) -> str:
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


def _download(url: str) -> Source:
    from assistant.board.docs import xlsx_sheet_rows
    from assistant.integrations import knowledge_google_docs as gdocs

    src = Source(url=url, label=_label(url))
    try:
        if src.is_sheet:
            src.xlsx = gdocs.fetch_google_sheet_xlsx(url, timeout=30)
            src.sheets = xlsx_sheet_rows(src.xlsx)
        else:
            src.text = str(gdocs.fetch_google_doc(url, timeout=30).text or "")
    except gdocs.GoogleDocNoAccess:
        src.error = "no_access"
    except Exception as e:  # сеть, таймаут, 5xx, битый файл
        print(f"[chat_links] fetch failed url={url} err={e!r}")
        src.error = "failed"
    return src


def load_sources(text: str, *, fetch: Optional[Callable[[str], Source]] = None) -> list[Source]:
    """Скачать ссылки из текста (с кэшем на несколько минут — второй шаг чата качает не заново)."""
    fetch = fetch or _download
    out: list[Source] = []
    now = time.monotonic()
    for url in find_links(text):
        with _CACHE_LOCK:
            hit = _CACHE.get(url)
        if hit and now - hit[0] < CACHE_TTL_SEC and hit[1].error != "failed":
            out.append(hit[1])
            continue
        src = fetch(url)
        with _CACHE_LOCK:
            _CACHE[url] = (time.monotonic(), src)
            for k in [k for k, (t, _s) in _CACHE.items() if time.monotonic() - t > CACHE_TTL_SEC]:
                _CACHE.pop(k, None)
        out.append(src)
    return out


def total_size(sources: list[Source]) -> int:
    return sum(s.size for s in sources)


def error_note(src: Source) -> str:
    if src.error == "no_access":
        return (
            f"{src.label} по ссылке {src.url} закрыта: доступа по ссылке нет, содержимое ты не видишь. "
            "Скажи об этом прямо и предложи открыть доступ «Все, у кого есть ссылка» (чтение) "
            "и прислать ссылку ещё раз — или выгрузить файл (.xlsx, .csv, .docx, PDF) и приложить. "
            "Не придумывай содержимое."
        )
    return (
        f"{src.label} по ссылке {src.url} сейчас не скачалась (ошибка сети или Google). "
        "Скажи, что не получилось её открыть, и предложи повторить или приложить файл. "
        "Не придумывай содержимое."
    )


def _inline_body(src: Source, limit: int) -> str:
    if src.is_sheet:
        from assistant.board.docs import extract_xlsx

        return extract_xlsx(src.xlsx, limit=limit) if src.xlsx else ""
    body = src.text
    if len(body) > limit:
        body = body[:limit] + f"\n…(документ обрезан: показано {limit} из {len(body)} символов)"
    return body


def prompt_notes_for(sources: list[Source]) -> str:
    """Блоки для промпта: содержимое (все листы таблицы) или объяснение, почему его нет."""
    notes: list[str] = []
    ok = [s for s in sources if not s.error]
    per = INLINE_LIMIT // max(1, len(ok))
    for src in sources:
        if src.error:
            notes.append(error_note(src))
            continue
        notes.append(
            f"{src.label} по ссылке {src.url} (скачана для тебя целиком, все листы; отвечай по её "
            "содержимому, не говори, что не умеешь открывать ссылки):\n" + _inline_body(src, per).strip()
        )
    return "\n\n".join(notes).strip()


def prompt_notes(text: str, *, fetch: Optional[Callable[[str], Source]] = None) -> str:
    return prompt_notes_for(load_sources(text, fetch=fetch))
