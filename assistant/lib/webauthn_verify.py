"""Проверка ответа WebAuthn (navigator.credentials.get) на сервере — без внешних библиотек WebAuthn.

Нужна сейфам в браузерах без PRF (Arc и др.): ключ, которым обёрнут ключ сейфов, лежит в браузере
(IndexedDB, неизвлекаемый), а саму обёртку сервер отдаёт только после проверенного касания Touch ID
или пароля устройства. Открытый ключ passkey сохраняется при регистрации из ``response.getPublicKey()``
(SPKI DER) — разбирать CBOR не нужно.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key

ALG_ES256 = -7
ALG_RS256 = -257
ALGS = (ALG_ES256, ALG_RS256)
FLAG_UP = 0x01
FLAG_UV = 0x04


class WebAuthnError(ValueError):
    pass


def allowed_rp_ids() -> set[str]:
    """Адреса, к которым можно привязать passkey сейфов: из настроек, а не из заголовка запроса (аудит 2026-11-10).

    ``VAULT_RP_IDS`` — через запятую; без него — адреса Орбиты и хост ``WEBAPP_PUBLIC_URL``. localhost и *.localhost —
    только для локальной разработки.
    """
    import os

    raw = os.getenv("VAULT_RP_IDS", "").strip()
    out = {h.strip().lower() for h in raw.split(",") if h.strip()}
    if not out:
        out = {"getorbita.me", "vault.getorbita.me"}  # адреса Орбиты: основной и отдельный для сейфов
        from assistant.lib.webapp_public import public_base_url

        host = (urlparse(public_base_url() or "").hostname or "").lower()
        if host:
            out.add(host)
    return out


def rp_id_allowed(rp_id: str) -> bool:
    h = str(rp_id or "").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    return h in allowed_rp_ids()


def b64u_decode(s: str) -> bytes:
    s = str(s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def check_public_key(spki_b64u: str, alg: int) -> None:
    """Ключ при регистрации: разбирается и подходит к алгоритму."""
    if alg not in ALGS:
        raise WebAuthnError("alg")
    try:
        key = load_der_public_key(b64u_decode(spki_b64u))
    except Exception as e:  # noqa: BLE001 — любой мусор = неверный ключ
        raise WebAuthnError("public_key") from e
    if alg == ALG_ES256 and not isinstance(key, ec.EllipticCurvePublicKey):
        raise WebAuthnError("public_key")
    if alg == ALG_RS256 and not isinstance(key, rsa.RSAPublicKey):
        raise WebAuthnError("public_key")


def verify_assertion(
    *,
    public_key: str,
    alg: int,
    rp_id: str,
    challenge: str,
    client_data_json: str,
    authenticator_data: str,
    signature: str,
    prev_sign_count: int = 0,
) -> int:
    """Проверить подпись; вернуть новый signCount. Любая ошибка — WebAuthnError с коротким кодом."""
    try:
        cdj = b64u_decode(client_data_json)
        auth = b64u_decode(authenticator_data)
        sig = b64u_decode(signature)
        cd: dict[str, Any] = json.loads(cdj)
    except Exception as e:  # noqa: BLE001
        raise WebAuthnError("format") from e
    if cd.get("type") != "webauthn.get":
        raise WebAuthnError("type")
    if str(cd.get("challenge") or "") != challenge:
        raise WebAuthnError("challenge")
    origin = urlparse(str(cd.get("origin") or ""))
    host = (origin.hostname or "").lower()
    if host != rp_id.lower() or (origin.scheme != "https" and host not in ("localhost",) and not host.endswith(".localhost")):
        raise WebAuthnError("origin")
    if len(auth) < 37:
        raise WebAuthnError("format")
    if auth[:32] != hashlib.sha256(rp_id.lower().encode()).digest():
        raise WebAuthnError("rp")
    flags = auth[32]
    if not (flags & FLAG_UP) or not (flags & FLAG_UV):
        raise WebAuthnError("uv")
    count = int.from_bytes(auth[33:37], "big")
    signed = auth + hashlib.sha256(cdj).digest()
    try:
        key = load_der_public_key(b64u_decode(public_key))
        if alg == ALG_ES256 and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == ALG_RS256 and isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise WebAuthnError("alg")
    except InvalidSignature as e:
        raise WebAuthnError("signature") from e
    except WebAuthnError:
        raise
    except Exception as e:  # noqa: BLE001
        raise WebAuthnError("public_key") from e
    # Счётчик: passkey Apple/iCloud всегда 0. Если растёт — не должен откатываться (клон ключа).
    if count and prev_sign_count and count <= prev_sign_count:
        raise WebAuthnError("counter")
    return count
