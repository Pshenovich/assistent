"""Вложения к комментариям обсуждения (фото и файлы)."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_FILES_PER_COMMENT = 8
IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)
TEXT_MIMES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "application/json",
        "application/xml",
        "text/xml",
    }
)
ALLOWED_EXT = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
    }
)

_FILE_BLOCK_RE = re.compile(
    r":::file\s+name=\"([^\"]+)\"\s*\n(.*?)(?:\n):::",
    re.DOTALL | re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def files_dir() -> Path:
    return notes_store._db_path().parent / "comment_files"


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_comment_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER,
            owner_user_id TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL DEFAULT 'application/octet-stream',
            size INTEGER NOT NULL DEFAULT 0,
            kind TEXT NOT NULL DEFAULT 'file',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_comment_files_comment "
        "ON share_comment_files(comment_id)"
    )
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "comment_id": (
            int(row["comment_id"]) if row["comment_id"] not in (None, "") else None
        ),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "item_kind": str(row["item_kind"] or ""),
        "item_id": str(row["item_id"] or ""),
        "author_user_id": str(row["author_user_id"] or ""),
        "filename": str(row["filename"] or "file"),
        "mime": str(row["mime"] or "application/octet-stream"),
        "size": int(row["size"] or 0),
        "kind": str(row["kind"] or "file"),
        "created_at": row["created_at"],
    }


def disk_path(file_id: int) -> Path:
    return files_dir() / f"{int(file_id)}.bin"


def kind_for(mime: str, filename: str) -> str:
    m = (mime or "").split(";")[0].strip().lower()
    if m in IMAGE_MIMES or m.startswith("image/"):
        return "image"
    ext = Path(filename or "").suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}:
        return "image"
    return "file"


def safe_filename(raw: str) -> str:
    name = Path(str(raw or "").strip()).name.replace("\x00", "")
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE).strip("._ ")
    return (name or "file")[:180]


def sniff_mime(data: bytes, filename: str, declared: str | None) -> str:
    declared_m = (declared or "").split(";")[0].strip().lower()
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    ext = Path(filename or "").suffix.lower()
    by_ext = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pdf": "application/pdf",
    }
    if declared_m and declared_m != "application/octet-stream":
        return declared_m
    return by_ext.get(ext, "application/octet-stream")


def validate_upload(filename: str, mime: str, size: int) -> None:
    if size <= 0:
        raise ValueError("Пустой файл")
    if size > MAX_FILE_BYTES:
        raise ValueError("Файл слишком большой (максимум 12 МБ)")
    ext = Path(filename or "").suffix.lower()
    m = (mime or "").split(";")[0].strip().lower()
    if ext and ext not in ALLOWED_EXT and not m.startswith("image/"):
        raise ValueError("Этот тип файла не поддерживается")


def save_bytes(
    *,
    owner_user_id: int | str,
    item_kind: str,
    item_id: str | int,
    author_user_id: int | str,
    filename: str,
    mime: str,
    data: bytes,
    comment_id: int | None = None,
) -> dict[str, Any]:
    name = safe_filename(filename)
    blob = data or b""
    sniffed = sniff_mime(blob, name, mime)
    validate_upload(name, sniffed, len(blob))
    kind = kind_for(sniffed, name)
    owner = _uid(owner_user_id)
    author = _uid(author_user_id)
    ts = _now_iso()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO share_comment_files (
                comment_id, owner_user_id, item_kind, item_id, author_user_id,
                filename, mime, size, kind, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(comment_id) if comment_id else None,
                owner,
                str(item_kind),
                str(item_id),
                author,
                name,
                sniffed,
                len(blob),
                kind,
                ts,
            ),
        )
        conn.commit()
        fid = int(cur.lastrowid)
    path = disk_path(fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    item = get_file(fid)
    if not item:
        raise RuntimeError("Не удалось сохранить файл")
    return item


def get_file(file_id: int) -> dict[str, Any] | None:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM share_comment_files WHERE id = ?",
            (int(file_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_for_comment(comment_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM share_comment_files
            WHERE comment_id = ?
            ORDER BY id ASC
            """,
            (int(comment_id),),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def list_for_comments(comment_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    ids = [int(x) for x in comment_ids if int(x) > 0]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    with _LOCK:
        cur = _conn().execute(
            f"SELECT * FROM share_comment_files WHERE comment_id IN ({q}) ORDER BY id ASC",
            ids,
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    for row in rows:
        cid = row.get("comment_id")
        if cid:
            out.setdefault(int(cid), []).append(row)
    return out


def link_to_comment(
    file_ids: list[int],
    *,
    comment_id: int,
    owner_user_id: int | str,
    item_kind: str,
    item_id: str | int,
    author_user_id: int | str,
) -> list[dict[str, Any]]:
    ids = [int(x) for x in file_ids if int(x) > 0]
    if not ids:
        return []
    if len(ids) > MAX_FILES_PER_COMMENT:
        raise ValueError(f"Слишком много файлов (максимум {MAX_FILES_PER_COMMENT})")
    owner = _uid(owner_user_id)
    author = _uid(author_user_id)
    kind = str(item_kind)
    iid = str(item_id)
    linked: list[dict[str, Any]] = []
    with _LOCK:
        conn = _conn()
        for fid in ids:
            cur = conn.execute(
                "SELECT * FROM share_comment_files WHERE id = ?",
                (fid,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Файл не найден")
            item = _row_to_dict(row)
            if item["owner_user_id"] != owner or item["item_kind"] != kind or item["item_id"] != iid:
                raise ValueError("Файл не относится к этой заметке")
            if item["author_user_id"] != author:
                raise ValueError("Нельзя прикрепить чужой файл")
            if item["comment_id"] not in (None, comment_id):
                raise ValueError("Файл уже прикреплён")
            conn.execute(
                "UPDATE share_comment_files SET comment_id = ? WHERE id = ?",
                (int(comment_id), fid),
            )
            item["comment_id"] = int(comment_id)
            linked.append(item)
        conn.commit()
    return linked


def delete_for_comment(comment_id: int) -> None:
    rows = list_for_comment(comment_id)
    with _LOCK:
        _conn().execute(
            "DELETE FROM share_comment_files WHERE comment_id = ?",
            (int(comment_id),),
        )
        _conn().commit()
    for row in rows:
        path = disk_path(int(row["id"]))
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def is_pdf(item: dict[str, Any]) -> bool:
    mime = str(item.get("mime") or "").split(";")[0].strip().lower()
    name = str(item.get("filename") or "").lower()
    return mime == "application/pdf" or name.endswith(".pdf")


def extract_text_preview(item: dict[str, Any], *, limit: int = 8000) -> str:
    if str(item.get("kind") or "") == "image":
        return ""
    mime = str(item.get("mime") or "").split(";")[0].strip().lower()
    name = str(item.get("filename") or "")
    ext = Path(name).suffix.lower()
    path = disk_path(int(item["id"]))
    if not path.is_file():
        return ""
    if ext == ".docx" or "wordprocessingml.document" in mime:
        from assistant.board.docs import extract_docx

        try:
            text = extract_docx(path.read_bytes())
        except Exception:
            return ""
        return text[:limit].strip()
    if mime not in TEXT_MIMES and ext not in {".txt", ".md", ".csv", ".json", ".xml"}:
        return ""
    raw = path.read_bytes()[: limit * 2]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return text[:limit].strip()


def parse_generated_file_blocks(answer: str) -> tuple[str, list[tuple[str, str]]]:
    """Вырезает :::file name="…"::: блоки. Возвращает (текст, [(имя, содержимое)])."""
    files: list[tuple[str, str]] = []

    def _repl(match: re.Match[str]) -> str:
        name = safe_filename(match.group(1))
        body = (match.group(2) or "").strip("\n")
        if name and body:
            files.append((name, body))
        return ""

    cleaned = _FILE_BLOCK_RE.sub(_repl, answer or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip(), files
