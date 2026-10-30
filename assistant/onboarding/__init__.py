"""Онбординг веб-приложения: каталог функций (catalog.json) — общий для тура, подсказок и Лео."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def features() -> list[dict[str, Any]]:
    return list(catalog().get("features") or [])


def feature_ids() -> set[str]:
    return {str(f["id"]) for f in features()}


def get_feature(feature_id: str) -> dict[str, Any] | None:
    fid = str(feature_id or "").strip()
    return next((f for f in features() if f.get("id") == fid), None)


def hinted_ids() -> set[str]:
    """Функции с подсказкой в интерфейсе (остальные — только в знаниях Лео)."""
    return {str(f["id"]) for f in features() if f.get("hint")}
