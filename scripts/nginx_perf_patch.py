#!/usr/bin/env python3
"""Идемпотентная правка живых nginx-конфигов Орбиты (деплой копирует конфиг, только если его ещё нет,
а certbot правит его на месте — поэтому новое доезжает этим скриптом).

Что делает в серверном блоке, где `location /` проксирует на приложение (127.0.0.1:8080):
- gzip для text/javascript (Python 3.12 отдаёт .js так — без этого app.js шёл несжатым);
- proxy_buffering on в `location /` (потоковых ответов нет; медленный клиент не держит uvicorn);
- client_max_body_size 64m по умолчанию; 520m — на загрузку записи встречи (приложение пишет её на диск
  по частям, лимит в нём — TRANSCRIPTION_UPLOAD_MAX_MB, 500), 60m — на голосовое (до 50 МБ, читается в память).
  Свой блок помечен «# leo-perf … end»: если он в конфиге устарел, заменяется на текущий.

  python3 scripts/nginx_perf_patch.py FILE [FILE …]   — правит на месте, печатает, что изменилось
  python3 scripts/nginx_perf_patch.py --check FILE …  — только сказать, нужна ли правка (код 1 — нужна)
"""

from __future__ import annotations

import re
import sys

MARK = "# leo-perf"
GZIP_NEED = ("text/javascript", "application/manifest+json")
TRANSCRIPTION_MAX = "520m"
VOICE_MAX = "60m"
DEFAULT_MAX = "64m"


def _blocks(text: str, head: str) -> list[tuple[int, int]]:
    """(начало, конец) блоков `head {` … `}` верхнего уровня с учётом вложенных скобок."""
    out = []
    for m in re.finditer(r"(?m)^[ \t]*" + re.escape(head) + r"\s*\{", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), i + 1))
                    break
            elif c == "#":
                nl = text.find("\n", i)
                i = len(text) if nl < 0 else nl
                continue
            i += 1
    return out


def _proxy_headers(indent: str) -> str:
    return "".join(
        f"{indent}{line}\n"
        for line in (
            "proxy_pass http://127.0.0.1:8080;",
            "limit_req zone=leo_rl_all burst=100 nodelay;",
            "limit_req_status 429;",
            "proxy_http_version 1.1;",
            "proxy_set_header Host $host;",
            "proxy_set_header X-Real-IP $remote_addr;",
            "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "proxy_set_header X-Forwarded-Proto $scheme;",
            "proxy_set_header X-Forwarded-Host $host;",
            "proxy_read_timeout 900s;",
            "proxy_send_timeout 900s;",
        )
    )


def _extra_locations(indent: str) -> str:
    i2 = indent + "    "
    return (
        f"{indent}{MARK}: лимит загрузок — scripts/nginx_perf_patch.py\n"
        f"{indent}# Запись встречи — большие файлы (приложение пишет их на диск); остальное — до {DEFAULT_MAX}.\n"
        f"{indent}location = /api/miniapp/notes/transcriptions/upload {{\n"
        f"{i2}client_max_body_size {TRANSCRIPTION_MAX};\n{_proxy_headers(i2)}{indent}}}\n"
        f"{indent}location = /api/miniapp/voice/transcribe {{\n"
        f"{i2}client_max_body_size {VOICE_MAX};\n{_proxy_headers(i2)}{indent}}}\n"
        f"{indent}{MARK} end\n"
    )


_MARKED_RE = re.compile(r"(?ms)^([ \t]*)" + re.escape(MARK) + r":.*?^[ \t]*" + re.escape(MARK) + r" end\n")


def patch(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []

    def fix_gzip(m: re.Match) -> str:
        types = m.group(2).split()
        add = [t for t in GZIP_NEED if t not in types]
        if not add:
            return m.group(0)
        changes.append("gzip_types +" + " ".join(add))
        return f"{m.group(1)}{' '.join(types + add)};"

    text = re.sub(r"(?m)^([ \t]*gzip_types\s+)([^;]*);", fix_gzip, text)

    # Серверные блоки с приложением — с конца, чтобы смещения не поехали.
    for start, end in reversed(_blocks(text, "server")):
        block = text[start:end]
        loc = None
        for ls, le in _blocks(block, "location /"):
            if "127.0.0.1:8080" in block[ls:le]:
                loc = (ls, le)
                break
        if loc is None:
            continue
        ls, le = loc
        body = block[ls:le]
        new_body = re.sub(r"(?m)^([ \t]*)proxy_buffering off;\n", r"\1proxy_buffering on;\n", body)
        if new_body != body:
            changes.append("location /: proxy_buffering on")
        indent = re.match(r"[ \t]*", body).group(0)
        block = block[:ls] + new_body + block[le:]
        m = _MARKED_RE.search(block)
        if m is None:
            block = block[:ls] + _extra_locations(indent) + "\n" + block[ls:]
            changes.append(f"лимит загрузок: {TRANSCRIPTION_MAX} на запись встречи, {VOICE_MAX} на голосовое")
        elif m.group(0) != _extra_locations(m.group(1)):
            block = block[: m.start()] + _extra_locations(m.group(1)) + block[m.end() :]
            changes.append(f"лимит загрузок обновлён: {TRANSCRIPTION_MAX} / {VOICE_MAX}")
        nb = re.sub(r"(?m)^([ \t]*client_max_body_size\s+)500m;", r"\g<1>" + DEFAULT_MAX + ";", block)
        if nb != block:
            changes.append(f"client_max_body_size 500m → {DEFAULT_MAX}")
            block = nb
        text = text[:start] + block + text[end:]
    return text, changes


def main(argv: list[str]) -> int:
    check = "--check" in argv
    files = [a for a in argv if a != "--check"]
    need = False
    for path in files:
        with open(path, encoding="utf-8") as f:
            old = f.read()
        new, changes = patch(old)
        if not changes:
            print(f"{path}: без изменений")
            continue
        need = True
        print(f"{path}: " + "; ".join(changes))
        if not check:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
    return 1 if (check and need) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
