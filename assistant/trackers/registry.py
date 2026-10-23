"""Реестр трекеров: новый трекер — модуль-адаптер и строка здесь."""

from __future__ import annotations

from assistant.trackers.base import TrackerError, TrackerProvider
from assistant.trackers.bitrix24 import Bitrix24Tracker

PROVIDERS: dict[str, TrackerProvider] = {
    "bitrix24": Bitrix24Tracker(),
}


def get_provider(provider_id: str) -> TrackerProvider:
    p = PROVIDERS.get(str(provider_id or "").strip())
    if p is None:
        raise TrackerError("Неизвестный трекер")
    return p


def connected_providers(uid: int) -> list[TrackerProvider]:
    out = []
    for p in PROVIDERS.values():
        try:
            if p.is_connected(uid):
                out.append(p)
        except Exception as e:
            print(f"[trackers] is_connected failed provider={p.id} uid={uid} err={e!r}")
    return out
