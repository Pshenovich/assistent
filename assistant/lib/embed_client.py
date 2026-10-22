"""Клиент локального сервиса эмбеддингов (assistant/services/embed_server.py)."""

from __future__ import annotations

import os

import requests


def enabled() -> bool:
    return (os.getenv("SEMANTIC_SEARCH_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _url() -> str:
    return (os.getenv("EMBED_URL", "http://127.0.0.1:8095") or "").rstrip("/")


def embed(texts: list[str], *, timeout: float = 120) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        r = requests.post(f"{_url()}/embed", json={"texts": texts[i : i + 64]}, timeout=timeout)
        r.raise_for_status()
        out.extend(r.json().get("vectors") or [])
    if len(out) != len(texts):
        raise RuntimeError("embed: число векторов не совпало с числом текстов")
    return out
