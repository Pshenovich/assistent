// Сейфы: шифрование на устройстве (WebCrypto). Сервер получает только то, что здесь запечатано.
//
// Ключи:
//   пара человека  — ECDH P-256; открытый ключ (JWK) лежит на сервере как есть;
//   UMK            — случайный AES-256 «ключ сейфов» человека; им запечатан закрытый ключ (PKCS#8);
//   обёртки UMK    — по одной на способ входа: passkey (HKDF от ответа PRF) и код с аварийного листа
//                    (PBKDF2 — код длинный и случайный, медленная функция — запас прочности);
//   VK             — AES-256 ключ сейфа; для каждого участника — конверт: ECDH с одноразовым ключом → HKDF → AES-GCM;
//   записи         — JSON полей, AES-GCM на VK, привязаны к id сейфа (AAD), чтобы шифр нельзя было перенести в другой сейф.
//
// Работает и в браузере, и в Node 18+ (globalThis.crypto) — тесты в tests/js/vault-crypto.test.mjs.
(function (root) {
  "use strict";

  var C = root.crypto;
  var S = C && C.subtle;
  var enc = new TextEncoder();
  var dec = new TextDecoder();
  var V = 1;
  var PBKDF2_ITER = 310000;
  var RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // без 0/O, 1/I — их путают на бумаге
  var RECOVERY_LEN = 24; // 24 × 5 бит = 120 бит

  function b64u(bytes) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var s = "";
    for (var i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function unb64u(str) {
    var s = String(str || "").replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function rand(n) {
    return C.getRandomValues(new Uint8Array(n));
  }

  function aes(raw, usages) {
    return S.importKey("raw", raw, { name: "AES-GCM" }, false, usages || ["encrypt", "decrypt"]);
  }

  // Запечатать байты ключом (raw или CryptoKey). Результат — JSON-строка {v, iv, ct}.
  async function seal(key, bytes, aad) {
    var k = key instanceof Uint8Array ? await aes(key) : key;
    var iv = rand(12);
    var p = { name: "AES-GCM", iv: iv };
    if (aad) p.additionalData = enc.encode(aad);
    var ct = await S.encrypt(p, k, bytes);
    return JSON.stringify({ v: V, iv: b64u(iv), ct: b64u(ct) });
  }
  async function open(key, sealed, aad) {
    var o = typeof sealed === "string" ? JSON.parse(sealed) : sealed;
    var k = key instanceof Uint8Array ? await aes(key) : key;
    var p = { name: "AES-GCM", iv: unb64u(o.iv) };
    if (aad) p.additionalData = enc.encode(aad);
    return new Uint8Array(await S.decrypt(p, k, unb64u(o.ct)));
  }

  async function hkdf(secret, salt, info) {
    var base = await S.importKey("raw", secret, "HKDF", false, ["deriveBits"]);
    var bits = await S.deriveBits({ name: "HKDF", hash: "SHA-256", salt: salt, info: enc.encode(info) }, base, 256);
    return new Uint8Array(bits);
  }

  // ---------- ключи человека ----------

  async function newIdentity() {
    var pair = await S.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var umk = rand(32);
    var pub = await S.exportKey("jwk", pair.publicKey);
    var pkcs8 = new Uint8Array(await S.exportKey("pkcs8", pair.privateKey));
    var publicJwk = JSON.stringify({ kty: pub.kty, crv: pub.crv, x: pub.x, y: pub.y });
    var encPrivate = await seal(umk, pkcs8, "orbita-vault:private");
    pkcs8.fill(0);
    return { umk: umk, publicKey: publicJwk, encPrivate: encPrivate };
  }

  // Открыть закрытый ключ; в памяти он остаётся неизвлекаемым CryptoKey.
  async function openPrivate(umk, encPrivate) {
    var pkcs8 = await open(umk, encPrivate, "orbita-vault:private");
    var key = await S.importKey("pkcs8", pkcs8, { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    pkcs8.fill(0);
    return key;
  }

  // ---------- passkey (WebAuthn PRF) ----------

  function newPrfSalt() {
    return b64u(rand(32));
  }
  async function prfKey(prfOutput, salt) {
    return hkdf(new Uint8Array(prfOutput), unb64u(salt), "orbita-vault:umk:passkey");
  }
  async function wrapUmkPrf(umk, prfOutput, salt) {
    return seal(await prfKey(prfOutput, salt), umk, "orbita-vault:umk");
  }
  async function unwrapUmkPrf(wrapped, prfOutput, salt) {
    return open(await prfKey(prfOutput, salt), wrapped, "orbita-vault:umk");
  }

  // ---------- аварийный код ----------

  function newRecoveryCode() {
    var bytes = rand(RECOVERY_LEN);
    var out = "";
    for (var i = 0; i < RECOVERY_LEN; i++) {
      out += RECOVERY_ALPHABET[bytes[i] & 31];
      if (i % 4 === 3 && i < RECOVERY_LEN - 1) out += "-";
    }
    return out;
  }
  function normalizeRecovery(code) {
    return String(code || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  }
  function recoveryLooksValid(code) {
    var n = normalizeRecovery(code);
    if (n.length !== RECOVERY_LEN) return false;
    for (var i = 0; i < n.length; i++) if (RECOVERY_ALPHABET.indexOf(n[i]) < 0) return false;
    return true;
  }
  async function recoveryKey(code, salt) {
    var base = await S.importKey("raw", enc.encode(normalizeRecovery(code)), "PBKDF2", false, ["deriveBits"]);
    var bits = await S.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: unb64u(salt), iterations: PBKDF2_ITER }, base, 256);
    return new Uint8Array(bits);
  }
  async function wrapUmkRecovery(umk, code) {
    var salt = b64u(rand(16));
    return { salt: salt, wrapped: await seal(await recoveryKey(code, salt), umk, "orbita-vault:umk") };
  }
  async function unwrapUmkRecovery(wrapped, code, salt) {
    return open(await recoveryKey(code, salt), wrapped, "orbita-vault:umk");
  }

  // ---------- ключ сейфа и конверты ----------

  function newVaultKey() {
    return rand(32);
  }
  async function importPublic(publicJwk) {
    var jwk = typeof publicJwk === "string" ? JSON.parse(publicJwk) : publicJwk;
    return S.importKey("jwk", { kty: "EC", crv: "P-256", x: jwk.x, y: jwk.y, ext: true }, { name: "ECDH", namedCurve: "P-256" }, false, []);
  }
  async function envKey(shared, epkJwk, recipientJwk) {
    // соль — оба открытых ключа: конверт нельзя «переадресовать» другому получателю
    var salt = enc.encode(epkJwk.x + epkJwk.y + "|" + recipientJwk.x + recipientJwk.y);
    return hkdf(new Uint8Array(shared), salt, "orbita-vault:envelope");
  }

  async function sealEnvelope(vk, recipientPublicJwk, aad) {
    var rj = typeof recipientPublicJwk === "string" ? JSON.parse(recipientPublicJwk) : recipientPublicJwk;
    var eph = await S.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var epk = await S.exportKey("jwk", eph.publicKey);
    var shared = await S.deriveBits({ name: "ECDH", public: await importPublic(rj) }, eph.privateKey, 256);
    var key = await envKey(shared, epk, rj);
    var sealed = JSON.parse(await seal(key, vk, aad || "orbita-vault:vk"));
    return JSON.stringify({ v: V, epk: { kty: "EC", crv: "P-256", x: epk.x, y: epk.y }, iv: sealed.iv, ct: sealed.ct });
  }

  async function openEnvelope(envelope, privateKey, myPublicJwk, aad) {
    var e = typeof envelope === "string" ? JSON.parse(envelope) : envelope;
    var me = typeof myPublicJwk === "string" ? JSON.parse(myPublicJwk) : myPublicJwk;
    var shared = await S.deriveBits({ name: "ECDH", public: await importPublic(e.epk) }, privateKey, 256);
    var key = await envKey(shared, e.epk, me);
    return open(key, e, aad || "orbita-vault:vk");
  }

  // ---------- подтвердить новое устройство с другого ----------
  // Новое устройство создаёт одноразовую пару ECDH и показывает код из её открытого ключа.
  // Устройство с ключами показывает код из ключа, который пришёл через сервер: совпали — сервер ключ не подменил.
  // UMK запечатывается этому ключу (как конверт, но с другим AAD).
  async function newLinkKey() {
    var pair = await S.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var pub = await S.exportKey("jwk", pair.publicKey);
    // закрытый ключ — заново неизвлекаемым
    var pkcs8 = await S.exportKey("pkcs8", pair.privateKey);
    var priv = await S.importKey("pkcs8", pkcs8, { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    return { publicKey: JSON.stringify({ kty: pub.kty, crv: pub.crv, x: pub.x, y: pub.y }), privateKey: priv };
  }
  async function linkCode(publicJwk) {
    var j = typeof publicJwk === "string" ? JSON.parse(publicJwk) : publicJwk;
    var h = new Uint8Array(await S.digest("SHA-256", enc.encode("orbita-vault:link|" + j.x + "|" + j.y)));
    var n = ((h[0] << 24) | (h[1] << 16) | (h[2] << 8) | h[3]) >>> 0;
    var c = String(n % 1000000);
    while (c.length < 6) c = "0" + c;
    return c.slice(0, 3) + " " + c.slice(3);
  }
  async function sealUmkForLink(umk, linkPublicJwk) {
    return sealEnvelope(umk, linkPublicJwk, "orbita-vault:umk:link");
  }
  async function openUmkFromLink(sealed, linkPrivate, linkPublicJwk) {
    return openEnvelope(sealed, linkPrivate, linkPublicJwk, "orbita-vault:umk:link");
  }

  // ---------- записи ----------

  // Формат 1 — AAD записи только с id сейфа. Формат 2 (аудит 2026-11-10): номер сейфа и номер записи придумывает
  // браузер, вместе с версией ключа они в AAD — сервер не переставит записи и не подсунет конверт от другого сейфа.
  // ctx = {fmt: 2, vault: <uid сейфа>, item: <uid записи>, kv: <версия ключа>}
  function itemAad(vaultId, ctx) {
    if (ctx && ctx.fmt === 2) return "orbita-vault:item:v2|" + ctx.vault + "|" + ctx.item + "|" + ctx.kv;
    return "orbita-vault:item:" + vaultId;
  }
  function envelopeAad(ctx) {
    return ctx && ctx.fmt === 2 ? "orbita-vault:vk:v2|" + ctx.vault + "|" + ctx.kv : "orbita-vault:vk";
  }
  function newUid() {
    return b64u(rand(16));
  }
  async function sealItem(vk, vaultId, data, ctx) {
    return seal(vk, enc.encode(JSON.stringify(data)), itemAad(vaultId, ctx));
  }
  async function openItem(vk, vaultId, blob, ctx) {
    return JSON.parse(dec.decode(await open(vk, blob, itemAad(vaultId, ctx))));
  }

  // ---------- генератор паролей ----------

  function generatePassword(len) {
    var n = Math.max(12, Math.min(64, len || 20));
    var sets = ["abcdefghijkmnopqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "-_.!@#%+="];
    var all = sets.join("");
    for (;;) {
      var bytes = rand(n * 2);
      var out = "";
      for (var i = 0; i < bytes.length && out.length < n; i++) {
        var b = bytes[i];
        if (b < 256 - (256 % all.length)) out += all[b % all.length]; // без перекоса по модулю
      }
      if (out.length === n && sets.every(function (s) { return out.split("").some(function (ch) { return s.indexOf(ch) >= 0; }); })) return out;
    }
  }

  // Браузеры без PRF (Arc): ключ устройства — AES, неизвлекаемый, живёт в IndexedDB этого браузера.
  // Им обёрнута UMK; обёртку сервер отдаёт только после проверенного касания (Touch ID / пароль устройства).
  function newDeviceKey() {
    return S.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function wrapUmkDevice(umk, deviceKey) {
    return seal(deviceKey, umk, "orbita-vault:umk:device");
  }
  async function unwrapUmkDevice(wrapped, deviceKey) {
    return open(deviceKey, wrapped, "orbita-vault:umk:device");
  }

  var api = {
    b64u: b64u, unb64u: unb64u, rand: rand,
    envelopeAad: envelopeAad, newUid: newUid,
    newLinkKey: newLinkKey, linkCode: linkCode, sealUmkForLink: sealUmkForLink, openUmkFromLink: openUmkFromLink,
    newDeviceKey: newDeviceKey, wrapUmkDevice: wrapUmkDevice, unwrapUmkDevice: unwrapUmkDevice,
    newIdentity: newIdentity, openPrivate: openPrivate,
    newPrfSalt: newPrfSalt, wrapUmkPrf: wrapUmkPrf, unwrapUmkPrf: unwrapUmkPrf,
    newRecoveryCode: newRecoveryCode, normalizeRecovery: normalizeRecovery, recoveryLooksValid: recoveryLooksValid,
    wrapUmkRecovery: wrapUmkRecovery, unwrapUmkRecovery: unwrapUmkRecovery,
    newVaultKey: newVaultKey, sealEnvelope: sealEnvelope, openEnvelope: openEnvelope,
    sealItem: sealItem, openItem: openItem, generatePassword: generatePassword,
    supported: !!(S && C.getRandomValues),
  };
  root.LeoVaultCrypto = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
