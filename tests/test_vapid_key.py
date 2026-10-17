"""VAPID private key: PEM из .env с литеральными \\n должен конвертироваться для pywebpush."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat


def test_vapid_pem_with_literal_newlines(monkeypatch):
    from assistant.services import discussion_notify as dn

    # Сбросить кэш между прогонами.
    dn._VAPID_PRIVATE_CACHE = None

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    escaped = pem.replace("\n", "\\n")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", escaped)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "x" * 87)

    out = dn.vapid_private_key()
    assert "BEGIN" not in out
    # raw urlsafe base64 без padding, 32 байта
    padded = out + ("=" * ((4 - len(out) % 4) % 4))
    raw = base64.urlsafe_b64decode(padded)
    assert len(raw) == 32
    assert dn.vapid_configured() is True

    dn._VAPID_PRIVATE_CACHE = None


def test_b64_pad():
    from assistant.services.discussion_notify import _b64_pad

    assert _b64_pad("abcd") == "abcd"
    assert len(_b64_pad("abc")) % 4 == 0
