"""Шифрование сейфов (webapp/vault-crypto.js) — прогон в Node: полный путь ключей и отказы при чужом ключе."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
require(process.argv[1]);
const V = globalThis.LeoVaultCrypto;
const out = {};
(async () => {
  // Вова включает сейфы: пара ключей, обёртки UMK для passkey и аварийного кода
  const me = await V.newIdentity();
  const prf = V.rand(32), salt = V.newPrfSalt();
  const wrappedPrf = await V.wrapUmkPrf(me.umk, prf, salt);
  const code = V.newRecoveryCode();
  const rec = await V.wrapUmkRecovery(me.umk, code);
  out.codeOk = V.recoveryLooksValid(code) && /^[A-Z2-9]{4}(-[A-Z2-9]{4}){5}$/.test(code);
  out.lowercaseOk = V.recoveryLooksValid(code.toLowerCase().replace(/-/g, " "));

  // вход по passkey и по коду дают ту же UMK
  const umk1 = await V.unwrapUmkPrf(wrappedPrf, prf, salt);
  const umk2 = await V.unwrapUmkRecovery(rec.wrapped, code.toLowerCase(), rec.salt);
  out.sameUmk = Buffer.from(umk1).equals(Buffer.from(me.umk)) && Buffer.from(umk2).equals(Buffer.from(me.umk));
  out.wrongPrf = await V.unwrapUmkPrf(wrappedPrf, V.rand(32), salt).then(() => false, () => true);
  out.wrongCode = await V.unwrapUmkRecovery(rec.wrapped, V.newRecoveryCode(), rec.salt).then(() => false, () => true);

  // сейф: ключ, конверт себе, запись
  const priv = await V.openPrivate(umk1, me.encPrivate);
  const vk = V.newVaultKey();
  const envMe = await V.sealEnvelope(vk, me.publicKey);
  const vkBack = await V.openEnvelope(envMe, priv, me.publicKey);
  out.envelopeOk = Buffer.from(vkBack).equals(Buffer.from(vk));
  const blob = await V.sealItem(vk, 7, { title: "Сервер прод", password: "s3cr3t" });
  out.blobHasNoSecret = !blob.includes("s3cr3t") && !blob.includes("Сервер");
  out.item = await V.openItem(vkBack, 7, blob);
  out.otherVault = await V.openItem(vk, 8, blob).then(() => false, () => true);

  // Дима получает конверт; чужим ключом его не открыть
  const dima = await V.newIdentity();
  const dimaPriv = await V.openPrivate(dima.umk, dima.encPrivate);
  const envDima = await V.sealEnvelope(vk, dima.publicKey);
  out.dimaOk = Buffer.from(await V.openEnvelope(envDima, dimaPriv, dima.publicKey)).equals(Buffer.from(vk));
  out.notForMe = await V.openEnvelope(envDima, priv, me.publicKey).then(() => false, () => true);
  out.redirected = await V.openEnvelope(envDima, dimaPriv, me.publicKey).then(() => false, () => true);
  out.encPrivateNotPlain = !me.encPrivate.includes("PRIVATE");

  // подтвердить новое устройство: UMK запечатана одноразовому ключу; код совпадает только для того же ключа
  const link = await V.newLinkKey();
  const sealedUmk = await V.sealUmkForLink(me.umk, link.publicKey);
  out.linkOk = Buffer.from(await V.openUmkFromLink(sealedUmk, link.privateKey, link.publicKey)).equals(Buffer.from(me.umk));
  const other = await V.newLinkKey();
  out.linkWrongKey = await V.openUmkFromLink(sealedUmk, other.privateKey, other.publicKey).then(() => false, () => true);
  out.linkNotEnvelope = await V.openEnvelope(sealedUmk, link.privateKey, link.publicKey).then(() => false, () => true);
  const c1 = await V.linkCode(link.publicKey), c2 = await V.linkCode(link.publicKey), c3 = await V.linkCode(other.publicKey);
  out.linkCode = /^[0-9]{3} [0-9]{3}$/.test(c1) && c1 === c2 && c1 !== c3;

  // формат 2: номер сейфа, номер записи и версия ключа в AAD
  const ctxA = { fmt: 2, vault: "VaultA", item: "ItemA", kv: 3 };
  const blob2 = await V.sealItem(vk, 7, { password: "x" }, ctxA);
  out.v2Ok = (await V.openItem(vk, 7, blob2, ctxA)).password === "x";
  out.v2OtherItem = await V.openItem(vk, 7, blob2, { ...ctxA, item: "ItemB" }).then(() => false, () => true);
  out.v2OldVersion = await V.openItem(vk, 7, blob2, { ...ctxA, kv: 2 }).then(() => false, () => true);
  out.v2NotV1 = await V.openItem(vk, 7, blob2).then(() => false, () => true);
  const env2 = await V.sealEnvelope(vk, me.publicKey, V.envelopeAad({ fmt: 2, vault: "VaultA", kv: 3 }));
  out.env2Ok = Buffer.from(await V.openEnvelope(env2, priv, me.publicKey, V.envelopeAad({ fmt: 2, vault: "VaultA", kv: 3 }))).equals(Buffer.from(vk));
  out.env2OtherVault = await V.openEnvelope(env2, priv, me.publicKey, V.envelopeAad({ fmt: 2, vault: "VaultB", kv: 3 })).then(() => false, () => true);
  out.env2AsV1 = await V.openEnvelope(env2, priv, me.publicKey).then(() => false, () => true);
  out.uid = /^[A-Za-z0-9_-]{20,}$/.test(V.newUid());

  const pw = V.generatePassword(20);
  out.pw = pw.length === 20 && /[a-z]/.test(pw) && /[A-Z]/.test(pw) && /[0-9]/.test(pw);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(not shutil.which("node"), reason="нужен node")
def test_vault_crypto_roundtrip() -> None:
    res = subprocess.run(
        ["node", "-e", SCRIPT, str(ROOT / "webapp" / "vault-crypto.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    item = out.pop("item")
    assert item == {"title": "Сервер прод", "password": "s3cr3t"}
    assert all(out.values()), out
