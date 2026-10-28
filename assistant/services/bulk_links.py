"""Большие таблицы и документы по ссылке из чата: обработка частями в фоне.

Когда данные по ссылкам не влезают в один запрос (chat_links.INLINE_LIMIT), чат не отвечает сразу:
заводит задачу, режет данные на части (у таблицы — по строкам листа, с заголовком), и для каждой
части модель делает промежуточный итог по просьбе пользователя. Клиент показывает прогресс
(GET /gpt/bulk/{id}), а когда все части готовы — повторяет запрос с bulk_job_id: модель получает
итоги всех частей вместо сырых данных и отвечает как обычно (в т.ч. правками заметки).

Задачи живут в памяти процесса (сервер один) не дольше JOB_TTL_SEC.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from assistant.lib.chat_links import Source, error_note

CHUNK_CHARS = 50_000
MAX_CHUNKS = 120
MAX_ROW_CHARS = 20_000
WORKERS = 5
JOB_TTL_SEC = 3 * 3600
PARTIAL_MAX_CHARS = 1800
MAP_TIMEOUT_SEC = 180


def map_model() -> str:
    return os.getenv("BULK_MAP_MODEL", "").strip() or "openai/gpt-4.1-mini"


@dataclass
class Chunk:
    label: str  # «Google Таблица …, лист «X», строки 2–31»
    text: str


@dataclass
class BulkJob:
    id: str
    uid: int
    instruction: str
    chunks: list[Chunk]
    notes: list[str]  # про закрытые/нескачанные ссылки
    sheets: list[str]
    rows: int
    created: float = field(default_factory=time.time)
    status: str = "running"  # running | done | error | cancelled
    done: int = 0
    failed: int = 0
    current: str = ""
    error: str = ""
    partials: list[Optional[str]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "done": self.done,
            "total": len(self.chunks),
            "failed": self.failed,
            "current": self.current,
            "sheets": len(self.sheets),
            "rows": self.rows,
            "error": self.error,
        }


_JOBS: dict[str, BulkJob] = {}
_LOCK = threading.Lock()


def _gc() -> None:
    now = time.time()
    with _LOCK:
        for jid in [j for j, job in _JOBS.items() if now - job.created > JOB_TTL_SEC]:
            _JOBS.pop(jid, None)


def get_job(job_id: str, uid: int) -> Optional[BulkJob]:
    with _LOCK:
        job = _JOBS.get(str(job_id or ""))
    return job if job and job.uid == int(uid) else None


def cancel_job(job_id: str, uid: int) -> bool:
    job = get_job(job_id, uid)
    if not job or job.status != "running":
        return False
    job.status = "cancelled"
    return True


def _split_rows(header: str, rows: list[str], label: str, start_no: int) -> list[Chunk]:
    """Строки листа — части не больше CHUNK_CHARS; заголовок повторяется в каждой части."""
    out: list[Chunk] = []
    buf: list[str] = []
    size = len(header)
    first = start_no
    no = start_no
    for row in rows:
        row = row if len(row) <= MAX_ROW_CHARS else row[:MAX_ROW_CHARS] + " …(строка обрезана)"
        if buf and size + len(row) + 1 > CHUNK_CHARS:
            out.append(Chunk(f"{label}, строки {first}–{no - 1}", "\n".join(([header] if header else []) + buf)))
            buf, size, first = [], len(header), no
        buf.append(row)
        size += len(row) + 1
        no += 1
    if buf:
        out.append(Chunk(f"{label}, строки {first}–{no - 1}", "\n".join(([header] if header else []) + buf)))
    return out


def make_chunks(sources: list[Source]) -> tuple[list[Chunk], list[str], int]:
    """(части, названия листов, всего строк данных)."""
    chunks: list[Chunk] = []
    sheets: list[str] = []
    rows_total = 0
    for src in sources:
        if src.error:
            continue
        if src.is_sheet:
            for title, rows in src.sheets:
                if not rows:
                    continue
                sheets.append(title)
                # Первая строка — заголовок, если в ней нет длинных текстов (иначе это уже данные).
                header = rows[0] if len(rows) > 1 and len(rows[0]) < 500 else ""
                data = rows[1:] if header else rows
                rows_total += len(data)
                chunks += _split_rows(header, data, f"{src.label}, лист «{title}»", 2 if header else 1)
        else:
            paras = [p for p in src.text.split("\n") if p.strip()]
            rows_total += len(paras)
            chunks += _split_rows("", paras, src.label, 1)
    return chunks[:MAX_CHUNKS], sheets, rows_total


def start_job(
    uid: int,
    instruction: str,
    sources: list[Source],
    *,
    username: str | None = None,
    complete: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> BulkJob:
    _gc()
    chunks, sheets, rows = make_chunks(sources)
    job = BulkJob(
        id=uuid.uuid4().hex[:16],
        uid=int(uid),
        instruction=str(instruction or "").strip()[:4000],
        chunks=chunks,
        notes=[error_note(s) for s in sources if s.error],
        sheets=sheets,
        rows=rows,
        partials=[None] * len(chunks),
    )
    with _LOCK:
        _JOBS[job.id] = job
    threading.Thread(
        target=_run_job, args=(job, username, complete), name=f"bulk-{job.id}", daemon=True
    ).start()
    print(f"[bulk_links] start job={job.id} uid={uid} chunks={len(chunks)} sheets={len(sheets)} rows={rows}")
    return job


_MAP_SYSTEM = """Ты помогаешь обработать большую таблицу/документ, которые целиком не помещаются в один запрос.
Данные разбиты на части; тебе дана ОДНА часть. По просьбе пользователя сделай промежуточный итог
только по этой части — потом итоги всех частей сложат в общий ответ.

Правила:
- Только то, что нужно для просьбы пользователя: подсчёты, списки, факты по каждой записи — кратко.
- Для подсчётов давай числа по этой части («Тема X — 4»), чтобы их можно было сложить с другими частями.
- Категории называй коротко и одинаково (именительный падеж, без кавычек), чтобы их можно было сопоставить.
- Если в части есть поля вроде клиента/оператора/компании, указывай их.
- Не делай выводов обо всей таблице, без вступлений и пояснений. Не больше 1500 символов.
- Если в части нет ничего нужного — ответь «нет данных»."""


def _map_chunk(job: BulkJob, i: int, username: str | None, complete: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user

    if job.status != "running":
        return
    chunk = job.chunks[i]
    job.current = chunk.label
    set_openrouter_usage_telegram_user(telegram_user_id=job.uid, telegram_username=username)
    payload = {
        "model": map_model(),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": _MAP_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Просьба пользователя: {job.instruction}\n\n"
                    f"Часть {i + 1} из {len(job.chunks)} — {chunk.label}:\n{chunk.text}"
                ),
            },
        ],
    }
    text = ""
    for attempt in range(2):
        try:
            data = complete(payload)
            text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if text:
                break
        except Exception as e:
            print(f"[bulk_links] map failed job={job.id} part={i + 1} try={attempt + 1} err={e!r}")
            time.sleep(2)
    with _LOCK:
        if text:
            job.partials[i] = text[:PARTIAL_MAX_CHARS]
        else:
            job.failed += 1
        job.done += 1


def _run_job(job: BulkJob, username: str | None, complete: Optional[Callable[[dict[str, Any]], dict[str, Any]]]) -> None:
    if complete is None:
        from assistant.integrations.openrouter_client import openrouter_chat_completion

        def complete(payload: dict[str, Any]) -> dict[str, Any]:
            return openrouter_chat_completion(payload, operation="bulk_links_map", timeout=MAP_TIMEOUT_SEC)

    try:
        if not job.chunks:
            job.status = "error"
            job.error = "Нет данных для обработки"
            return
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(lambda i: _map_chunk(job, i, username, complete), range(len(job.chunks))))
        if job.status != "running":
            return
        if job.failed >= len(job.chunks):
            job.status = "error"
            job.error = "Не получилось обработать ни одной части"
            return
        job.status = "done"
        job.current = ""
        print(f"[bulk_links] done job={job.id} parts={len(job.chunks)} failed={job.failed}")
    except Exception as e:
        print(f"[bulk_links] job failed job={job.id} err={e!r}")
        job.status = "error"
        job.error = str(e)[:300]


def result_notes(job: BulkJob) -> str:
    """Что получает модель на втором шаге вместо сырых данных: итоги всех частей."""
    head = (
        f"Данные по ссылке были слишком большими для одного запроса ({job.rows} строк, листов: "
        f"{len(job.sheets)}{': ' + ', '.join(job.sheets) if job.sheets else ''}), поэтому их уже обработали "
        f"целиком по частям ({len(job.chunks)} частей). Ниже — промежуточные итоги каждой части по просьбе "
        "пользователя. Это ПОЛНЫЙ охват всех строк: собери общий ответ по всем частям — сложи числа, объедини "
        "одинаковые и близкие по смыслу категории. Не говори, что видел только часть данных, и не проси прислать их снова."
    )
    if job.failed:
        head += f" Внимание: {job.failed} из {len(job.chunks)} частей обработать не удалось — скажи об этом пользователю."
    parts = [
        f"[{i + 1}. {c.label}]\n{p}"
        for i, (c, p) in enumerate(zip(job.chunks, job.partials))
        if p and p.strip().lower() != "нет данных"
    ]
    body = head + "\n\n" + "\n\n".join(parts)
    if job.notes:
        body += "\n\n" + "\n\n".join(job.notes)
    return body.strip()
