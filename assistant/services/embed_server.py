"""Локальный сервис эмбеддингов (B6): одна модель в памяти на всех — бот и API ходят по HTTP.

Запуск: uvicorn assistant.services.embed_server:app --host 127.0.0.1 --port 8095
Модель: EMBED_MODEL (по умолчанию multilingual MiniLM, 384 измерения, ONNX через fastembed).
"""

from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

app = FastAPI(title="assistant-embed")
_MODEL: Any = None
_LOCK = threading.Lock()


def _model() -> Any:
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from fastembed import TextEmbedding

            _MODEL = TextEmbedding(
                model_name=os.getenv("EMBED_MODEL", DEFAULT_MODEL),
                cache_dir=os.getenv("EMBED_CACHE_DIR", "/opt/assistant/data/embed_models"),
                threads=int(os.getenv("EMBED_THREADS", "1") or "1"),
            )
        return _MODEL


class _EmbedBody(BaseModel):
    texts: list[str]


@app.on_event("startup")
def _warm() -> None:
    _model()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "model": os.getenv("EMBED_MODEL", DEFAULT_MODEL)}


@app.post("/embed")
def embed(body: _EmbedBody) -> dict[str, Any]:
    texts = [str(t or "")[:4000] for t in body.texts[:128]]
    vecs = [[round(float(x), 6) for x in v] for v in _model().embed(texts, batch_size=16)]
    return {"vectors": vecs, "dim": len(vecs[0]) if vecs else 0}
