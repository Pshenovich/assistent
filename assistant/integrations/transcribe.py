"""Obuchat transcription API."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

from assistant.config import OBUCHAT_TRANSCRIBE_BASE


@dataclass(frozen=True)
class TranscribeResult:
    plain_text: str
    formatted_text: str
    job_id: str | None = None


def _api_key() -> str:
    k = os.getenv("OBUCHAT_TRANSCRIBE", "").strip()
    if not k:
        raise RuntimeError("Не задан OBUCHAT_TRANSCRIBE в .env")
    return k


def _speakers_mode() -> str:
    return (os.getenv("OBUCHAT_SPEAKERS", "auto") or "auto").strip() or "auto"


def _plain_text_from_body(body: dict[str, Any]) -> str:
    for key in ("transcript", "text", "result"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    dia = body.get("diarization")
    if isinstance(dia, list):
        parts = [str(x.get("text") or "").strip() for x in dia if isinstance(x, dict)]
        joined = "\n".join(p for p in parts if p)
        if joined:
            return joined
    return ""


def _speaker_label(seg: dict[str, Any]) -> str:
    for key in ("speaker", "speaker_id", "label", "name"):
        v = seg.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "Спикер"


def _formatted_from_diarization(dia: list[Any]) -> str:
    blocks: list[str] = []
    for item in dia:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        label = _speaker_label(item)
        if label.isdigit():
            label = f"Спикер {label}"
        elif not label.lower().startswith("спикер"):
            label = f"Спикер {label}"
        blocks.append(f"{label}:\n{text}")
    return "\n\n".join(blocks)


def _result_from_body(body: dict[str, Any], *, job_id: str | None = None) -> TranscribeResult | None:
    plain = _plain_text_from_body(body)
    dia = body.get("diarization")
    formatted = ""
    if isinstance(dia, list) and dia:
        formatted = _formatted_from_diarization(dia)
    if not formatted:
        formatted = plain
    if not plain and not formatted:
        return None
    if not plain:
        plain = formatted
    return TranscribeResult(
        plain_text=plain,
        formatted_text=formatted or plain,
        job_id=job_id,
    )


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
