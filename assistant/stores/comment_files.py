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
    r":::file\s+name\s*=\s*[\"']([^\"']+)[\"']\s*\n(.*?)(?:\n):::",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_FILE_RE = re.compile(
    r"```file:([^\n`]+)\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_IMAGE_BLOCK_RE = re.compile(
    r":::image(?:\s+prompt\s*=\s*[\"']([^\"']+)[\"'])?\s*\n?(.*?)(?:\n)?:::",
    re.DOTALL | re.IGNORECASE,
)
_IMAGE_INTENT_RE = re.compile(
    r"(нарисуй|нарисовать|нарисуйте|апскейл|upscale|"
    r"увелич\w*|масштаб|dpi|"
    r"сгенер\w{0,8}\s+(?:мне\s+)?(?:картинк|изображен|фото|иллюстрац|мем|логотип|png)|"
    r"(?:сделай|создай|придумай|пришли|скинь|вышли|отправь|сохрани)\s+(?:мне\s+)?"
    r"(?:картинк|изображен|фото|иллюстрац|мем|логотип|баннер|png|jpe?g|файл)|"
    r"[×xх]\s*[2348]\b|"
    r"\b(?:draw|generate|create|make|send|export|upscale)\b.{0,48}\b(?:an?\s+)?"
    r"(?:image|picture|photo|logo|illustration|png|jpe?g)\b)",
    re.IGNORECASE | re.DOTALL,
)
_DESCRIBE_PHOTO_RE = re.compile(
    r"^\s*(что (на|в)|опиши|распознай|прочитай).{0,40}(фото|картинк|изображен)",
    re.IGNORECASE,
)
_REFUSAL_RE = re.compile(
    r"base64|разобью на части|вложен\w*.{0,24}не проход|"
    r"не могу (присл|отправ|влож|сгенер)|подтвердите|"
    r"скрипт под вашу ОС|сохраните как",
    re.IGNORECASE,
)
_CLAIMED_ATTACH_RE = re.compile(
    r"файл во вложении|во вложении\.|прикрепил\b|attached (?:the )?(?:file|image)|here(?:'s| is) (?:the )?(?:file|image)",
    re.IGNORECASE,
)
_SEND_AGAIN_RE = re.compile(
    r"(еще|ещё)\s+раз|отправ\w*|пришли|скинь|вышли|send\s+again|resend",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(r"^\s*(да|yes|ок|ok|подтверждаю|начинай|ага)\s*[.!]?\s*$", re.IGNORECASE)
_EMBEDDED_PNG_RE = re.compile(
    r"(?:data:image/png;base64,)?(iVBORw0KGgo[A-Za-z0-9+/=\s]{80,})",
    re.IGNORECASE,
)
_EMBEDDED_JPEG_RE = re.compile(
    r"(?:data:image/jpeg;base64,)?(/9j/[A-Za-z0-9+/=\s]{80,})",
    re.IGNORECASE,
)
_IMAGE_NAME_RE = re.compile(
    r"(?:^|[\s(«\"'])([A-Za-z0-9._-]+\.(?:png|jpe?g|webp|gif))\b",
    re.IGNORECASE,
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


def list_recent_images(
    *,
    owner_user_id: int | str,
    item_kind: str,
    item_id: str | int,
    limit: int = 2,
) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    lim = max(1, min(int(limit), 8))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM share_comment_files
            WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?
              AND kind = 'image'
            ORDER BY id DESC
            LIMIT ?
            """,
            (owner, str(item_kind), str(item_id), lim),
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
    """Вырезает :::file name="…"::: и ```file:имя блоки. Возвращает (текст, [(имя, содержимое)])."""
    files: list[tuple[str, str]] = []

    def _take(name: str, body: str) -> str:
        fname = safe_filename(name)
        content = (body or "").strip("\n")
        if fname and content:
            files.append((fname, content))
        return ""

    def _repl_colon(match: re.Match[str]) -> str:
        return _take(match.group(1), match.group(2))

    cleaned = _FILE_BLOCK_RE.sub(_repl_colon, answer or "")
    cleaned = _FENCE_FILE_RE.sub(_repl_colon, cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip(), files


def parse_generated_image_prompts(answer: str) -> tuple[str, list[str]]:
    """Вырезает :::image prompt="…"::: блоки. Возвращает (текст, [промпты])."""
    prompts: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        attr = (match.group(1) or "").strip()
        body = (match.group(2) or "").strip()
        prompt = attr or body
        if prompt:
            prompts.append(prompt[:2000])
        return ""

    cleaned = _IMAGE_BLOCK_RE.sub(_repl, answer or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip(), prompts


def wants_generated_image(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _DESCRIBE_PHOTO_RE.search(raw) and not _IMAGE_INTENT_RE.search(raw):
        return False
    return bool(_IMAGE_INTENT_RE.search(raw))


def wants_image_delivery(
    text: str,
    *,
    has_source: bool = False,
    answer: str = "",
) -> bool:
    if looks_like_attachment_refusal(answer) or looks_like_attachment_refusal(text):
        return True
    if claims_attachment_ready(answer) or claims_attachment_ready(text):
        return True
    if wants_resend_attachment(text):
        return True
    if wants_generated_image(text):
        return True
    raw = (text or "").strip()
    if has_source and re.search(
        r"png|jpe?g|webp|файл|вложен|dpi|апскейл|upscale|увелич|масштаб|[×xх]\s*\d",
        raw,
        re.I,
    ):
        if _DESCRIBE_PHOTO_RE.search(raw) and not _IMAGE_INTENT_RE.search(raw):
            return False
        return True
    return False


def looks_like_attachment_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.search(text or ""))


def claims_attachment_ready(text: str) -> bool:
    return bool(_CLAIMED_ATTACH_RE.search(text or ""))


def wants_resend_attachment(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if _SEND_AGAIN_RE.search(raw) and re.search(
        r"файл|фото|картинк|изображен|png|jpe?g|вложен|image|file",
        raw,
        re.I,
    ):
        return True
    if re.search(r"отправ\w*|пришли|скинь|вышли", raw, re.I) and re.search(
        r"файл|фото|картинк|png|jpe?g|вложен", raw, re.I
    ):
        return True
    return False


def visual_image_prompt(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    has_source: bool = False,
) -> str:
    """Промпт для image-модели: не «отправь файл», а визуальное задание."""
    q = (question or "").strip()
    prior = ""
    for it in reversed(history or []):
        if str((it or {}).get("role") or "").strip().lower() != "user":
            continue
        content = str((it or {}).get("content") or "").strip()
        if not content or is_bare_confirm(content) or wants_resend_attachment(content):
            continue
        if wants_generated_image(content) or wants_image_delivery(content, has_source=has_source):
            prior = content
            break
    base = prior or q or "create the requested image"
    if has_source:
        return (
            "Edit the attached photo. Keep the same subject and composition. "
            f"User request: {base}"
        )[:2000]
    return (
        "Create a single PNG image for this request. "
        f"User request: {base}"
    )[:2000]


def is_bare_confirm(text: str) -> bool:
    return bool(_CONFIRM_RE.match(text or ""))


def suggested_image_filename(text: str) -> str | None:
    match = _IMAGE_NAME_RE.search(text or "")
    if not match:
        return None
    return safe_filename(match.group(1))


def extract_embedded_images(answer: str) -> tuple[str, list[dict[str, str]]]:
    """Достаёт PNG/JPEG из data URL или сырого base64 в тексте ответа."""
    import base64

    images: list[dict[str, str]] = []

    def _take(blob: str, mime: str) -> str:
        compact = re.sub(r"\s+", "", blob or "")
        if len(compact) < 80:
            return ""
        try:
            data = base64.b64decode(compact, validate=False)
        except Exception:
            return blob
        if not data:
            return blob
        images.append(
            {"mime": mime, "b64": base64.b64encode(data).decode("ascii")}
        )
        return ""

    def _png(match: re.Match[str]) -> str:
        return _take(match.group(1), "image/png")

    def _jpg(match: re.Match[str]) -> str:
        return _take(match.group(1), "image/jpeg")

    cleaned = _EMBEDDED_PNG_RE.sub(_png, answer or "")
    cleaned = _EMBEDDED_JPEG_RE.sub(_jpg, cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip(), images


def decode_generated_file_bytes(filename: str, body: str) -> tuple[bytes, str] | None:
    """Если :::file с картинкой содержит base64 — вернуть байты. Иначе None."""
    import base64

    ext = Path(filename or "").suffix.lower()
    mime_by_ext = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    if ext not in mime_by_ext:
        return None
    raw = (body or "").strip()
    if raw.lower().startswith("data:image") and "," in raw:
        raw = raw.split(",", 1)[1]
    compact = re.sub(r"\s+", "", raw)
    if len(compact) < 80:
        return None
    try:
        data = base64.b64decode(compact, validate=False)
    except Exception:
        return None
    if not data:
        return None
    return data, mime_by_ext[ext]
