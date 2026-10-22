"""Локальный сервис эмбеддингов (B6): одна модель в памяти на всех — бот и API ходят по HTTP.

Запуск: uvicorn assistant.services.embed_server:app --host 127.0.0.1 --port 8095
Модель: EMBED_MODEL (по умолчанию multilingual MiniLM, 384 измерения). fastembed только скачивает
ONNX-файлы; векторы считаем сами (mean pooling) — см. _MeanPoolEncoder.
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


class _MeanPoolEncoder:
    """ONNX-модель + токенизатор с mean pooling по attention mask и L2-нормой.

    fastembed для paraphrase-multilingual-MiniLM пулит не так, как модель обучали: на проверке
    6 запросов верный документ был первым в 3 случаях, с mean pooling — в 5.
    """

    def __init__(self, model_dir: str, threads: int) -> None:
        import glob

        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx = sorted(glob.glob(os.path.join(model_dir, "**", "*.onnx"), recursive=True))[0]
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tok.enable_padding()
        self.tok.enable_truncation(int(os.getenv("EMBED_MAX_TOKENS", "256") or "256"))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(onnx, sess_options=opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        import numpy as np

        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            enc = self.tok.encode_batch(texts[i : i + batch_size])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            v = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
            v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
            out.extend(v.tolist())
        return out


def _model_dir(cache_dir: str, model_name: str) -> str:
    """Скачать модель через fastembed (если ещё нет) и вернуть каталог с tokenizer.json."""
    import glob

    def find() -> str:
        tail = model_name.split("/")[-1].lower()
        for path in glob.glob(os.path.join(cache_dir, "**", "tokenizer.json"), recursive=True):
            if tail in path.lower():
                return os.path.dirname(path)
        return ""

    found = find()
    if not found:
        from fastembed import TextEmbedding

        TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=1)  # только загрузка файлов
        found = find()
    if not found:
        raise RuntimeError(f"Не нашёл файлы модели {model_name} в {cache_dir}")
    return found


def _model() -> Any:
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            cache = os.getenv("EMBED_CACHE_DIR", "/opt/assistant/data/embed_models")
            name = os.getenv("EMBED_MODEL", DEFAULT_MODEL)
            _MODEL = _MeanPoolEncoder(_model_dir(cache, name), int(os.getenv("EMBED_THREADS", "1") or "1"))
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
    vecs = [[round(float(x), 6) for x in v] for v in _model().embed(texts)]
    return {"vectors": vecs, "dim": len(vecs[0]) if vecs else 0}
