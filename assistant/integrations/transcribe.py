"""Obuchat transcription API."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from assistant.config import OBUCHAT_TRANSCRIBE_BASE


# «SPEAKER_1:», «Speaker_0», «[Спикер 2]:» — метки говорящего от транскрайбера.
_SPEAKER_LABEL_RE = re.compile(
    r"(^|(?<=\s))[\[(]?(?:speaker|спикер)[ _-]?(\d+)[\])]?\s*[:：.\-—]?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)


def strip_speaker_labels(text: str) -> str:
    """Текст без меток говорящего (для команд боту, диктовки, напоминаний)."""
    t = _SPEAKER_LABEL_RE.sub(r"\1", str(text or ""))
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _single_speaker(text: str) -> bool:
    ids = {m.group(2) for m in _SPEAKER_LABEL_RE.finditer(str(text or ""))}
    return len(ids) <= 1


@dataclass(frozen=True)
class TranscribeResult:
    plain_text: str
    formatted_text: str
    job_id: str | None = None
    # ({"start": сек, "end": сек, "speaker": "Спикер 1", "text": …}, …) — по времени;
    # speaker пустой, если говорящий один.
    segments: tuple = ()


def _api_key() -> str:
    k = os.getenv("OBUCHAT_TRANSCRIBE", "").strip()
    if not k:
        raise RuntimeError("Не задан OBUCHAT_TRANSCRIBE в .env")
    return k


def _speakers_mode() -> str:
    return (os.getenv("OBUCHAT_SPEAKERS", "auto") or "auto").strip() or "auto"


_SPEAKER_ID_RE = re.compile(r"^(?:speaker|спикер)[ _-]?(\d+)$", re.IGNORECASE)


def _speaker_name(seg: dict[str, Any]) -> str:
    """«SPEAKER_1» / «1» / «Спикер 1» → «Спикер 1»; имя (Zoom) — как есть."""
    for key in ("speaker", "speaker_id", "label", "name"):
        v = seg.get(key)
        if v is None or not str(v).strip():
            continue
        raw = str(v).strip()
        if raw.isdigit():
            return f"Спикер {raw}"
        m = _SPEAKER_ID_RE.match(raw)
        return f"Спикер {m.group(1)}" if m else raw
    return ""


def _seconds(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _segments_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Сегменты из ответа: сейчас это список в «transcript», раньше — «diarization»."""
    items: list[Any] = []
    for key in ("transcript", "diarization", "segments"):
        v = body.get(key)
        if isinstance(v, list) and v:
            items = v
            break
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": _seconds(item.get("start")),
            "end": _seconds(item.get("end")),
            "speaker": _speaker_name(item),
            "text": text,
        })
    if out and all(s["start"] is not None for s in out):
        out.sort(key=lambda s: s["start"])  # у Obuchat реплики разных спикеров перекрываются
    if len({s["speaker"] for s in out}) <= 1:
        for s in out:
            s["speaker"] = ""
    return out


def formatted_from_segments(segments: list[dict[str, Any]]) -> str:
    """«Спикер 1:\n…» блоками; подряд идущие реплики одного спикера — в один блок."""
    blocks: list[tuple[str, list[str]]] = []
    for s in segments:
        sp = str(s.get("speaker") or "")
        if blocks and blocks[-1][0] == sp:
            blocks[-1][1].append(str(s.get("text") or ""))
        else:
            blocks.append((sp, [str(s.get("text") or "")]))
    parts = []
    for sp, texts in blocks:
        text = " ".join(t for t in texts if t)
        parts.append(f"{sp}:\n{text}" if sp else text)
    return "\n\n".join(parts)


def _plain_text_from_body(body: dict[str, Any]) -> str:
    for key in ("transcript", "text", "result"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _result_from_body(body: dict[str, Any], *, job_id: str | None = None) -> TranscribeResult | None:
    segments = _segments_from_body(body)
    if segments:
        formatted = formatted_from_segments(segments)
        plain = "\n".join(s["text"] for s in segments)
        return TranscribeResult(
            plain_text=plain, formatted_text=formatted, job_id=job_id, segments=tuple(segments)
        )
    plain = _plain_text_from_body(body)
    if not plain:
        return None
    # Obuchat отдаёт «SPEAKER_1: …» и в «чистом» тексте: убираем метки,
    # а в размеченном — только если говорящий один (иначе метки нужны).
    formatted = plain
    plain = strip_speaker_labels(plain)
    if _single_speaker(formatted):
        formatted = strip_speaker_labels(formatted)
    return TranscribeResult(plain_text=plain, formatted_text=formatted or plain, job_id=job_id)


def _post_timeout_sec() -> float:
    try:
        return float(os.getenv("OBUCHAT_TRANSCRIBE_POST_TIMEOUT_SEC", "600") or "600")
    except ValueError:
        return 600.0


def _poll_timeout_sec() -> float:
    try:
        return float(os.getenv("OBUCHAT_TRANSCRIBE_POLL_TIMEOUT_SEC", "900") or "900")
    except ValueError:
        return 900.0


def _verify_ssl() -> bool:
    raw = (os.getenv("OBUCHAT_TRANSCRIBE_VERIFY_SSL", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _request_kwargs() -> dict[str, Any]:
    return {"verify": _verify_ssl()}


def transcribe_bytes(audio: bytes, filename: str, *, speakers: str | None = None) -> TranscribeResult:
    headers = {"X-API-Key": _api_key()}
    fn = filename
    if fn.endswith(".oga"):
        fn = fn[:-4] + ".ogg"
    url = f"{OBUCHAT_TRANSCRIBE_BASE}/transcribe?wait=true"
    files = {"file": (fn, audio, "application/octet-stream")}
    mode = (speakers or _speakers_mode()).strip() or "auto"
    data = {"speakers": mode, "llm_enabled": "true"}
    post_timeout = _post_timeout_sec()
    req_kwargs = _request_kwargs()
    try:
        r = requests.post(
            url,
            headers=headers,
            files=files,
            data=data,
            timeout=post_timeout,
            **req_kwargs,
        )
    except requests.Timeout as e:
        raise RuntimeError(
            f"Транскрибация: сервис не ответил за {int(post_timeout)} с. "
            "Попробуйте позже или отправьте файл короче."
        ) from e
    except requests.exceptions.SSLError as e:
        raise RuntimeError(
            "Транскрибация: истёк SSL-сертификат transcribe.obuchat.me. "
            "Обновите сертификат на сервере транскрибации или временно задайте "
            "OBUCHAT_TRANSCRIBE_VERIFY_SSL=0 в .env."
        ) from e
    body = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"Транскрибация HTTP {r.status_code}: {body!r}")
    if not isinstance(body, dict):
        raise RuntimeError("Неверный ответ транскрибации")
    job_id = str(body.get("job_id") or "").strip() or None
    if body.get("status") in ("done", None):
        out = _result_from_body(body, job_id=job_id)
        if out:
            return out
    if not job_id:
        raise RuntimeError(f"Нет текста в ответе: {body!r}")
    poll_timeout = _poll_timeout_sec()
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            jr = requests.get(
                f"{OBUCHAT_TRANSCRIBE_BASE}/jobs/{job_id}",
                headers=headers,
                timeout=90,
                **req_kwargs,
            )
        except requests.Timeout as e:
            raise RuntimeError(
                f"Транскрибация: таймаут ожидания результата ({int(poll_timeout)} с)."
            ) from e
        jr.raise_for_status()
        data2 = jr.json()
        if isinstance(data2, dict) and data2.get("status") in ("done", "failed"):
            out = _result_from_body(data2, job_id=job_id)
            if out:
                return out
            raise RuntimeError("Транскрибация завершилась без текста")
    raise RuntimeError(
        f"Транскрибация: таймаут ожидания результата ({int(poll_timeout)} с)."
    )
