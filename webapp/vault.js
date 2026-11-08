// Сейфы в базе знаний: пароли и ключи, зашифрованные на устройстве (шифрование — vault-crypto.js, сервер — /vaults).
//
// На компьютере сейфы живут в дереве и на страницах базы знаний (org-kb-desk.js зовёт treeHtml / homeHtml / render),
// на телефоне — блок в списке вкладки и экран поверх неё (openMobile).
//
// Что держим в памяти и только в памяти: закрытый ключ человека (неизвлекаемый CryptoKey) и ключи открытых сейфов.
// Через 15 минут без действий или по «Закрыть» всё стирается. В localStorage и на сервер ключи не попадают.
// Содержимое записей не уходит в Лео, поиск, аналитику и логи (правило 17).
(function () {
  "use strict";

  var IDLE_MS = 15 * 60 * 1000;
  var CLIP_MS = 30 * 1000;
  var CR = window.LeoVaultCrypto;

  var st = {
    loaded: false,
    loading: null,
    enabled: false, // сейфы включены на сервере (флаг VAULTS_ENABLED)
    keys: null, // {enabled, public_key, enc_private, unlockers}
    vaults: [],
    priv: null, // CryptoKey — пока сейфы открыты
    vk: {}, // vaultId → Uint8Array
    vkVer: {}, // vaultId → версия ключа, которым открыт сейф (после смены ключа — открыть заново)
    rotating: {}, // vaultId → идёт смена ключа
    events: {}, // vaultId → журнал сейфа (аварийный доступ)
    items: {}, // vaultId → [{id, data, updated_at, updated_by_name}]
    lastActive: 0,
    view: "list", // list | record | edit | setup | code | locked | device | add-device
    vaultId: null,
    itemId: null,
    draft: null, // правка записи
    setup: null, // {identity, code, passkey}
    reveal: {}, // itemId → true (пароль показан)
    log: {}, // itemId → журнал
    busy: false,
    mobile: null, // экран на телефоне
    after: null, // что сделать после открытия (например, создать сейф)
  };

  var TYPES = {
    server: { label: "Сервер", fields: [["address", "Адрес", "161.104.35.44 или host:port"], ["login", "Логин", "root"], ["password", "Пароль", "", "secret"], ["note", "Заметка", "", "text"]] },
    site: { label: "Сайт или админка", fields: [["address", "Ссылка", "https://"], ["login", "Логин", "name@company.ru"], ["password", "Пароль", "", "secret"], ["note", "Заметка", "", "text"]] },
    api: { label: "API-ключ", fields: [["address", "Сервис", "openrouter.ai"], ["password", "Ключ", "", "secret"], ["expires", "Действует до", "", "date"], ["note", "Заметка", "", "text"]] },
    note: { label: "Заметка", fields: [["note", "Текст", "", "text-secret"]] },
  };
  var ROLE_LABEL = { viewer: "Смотрит", editor: "Правит", owner: "Владелец" };
  var RANK = { viewer: 1, editor: 2, owner: 3 };

  var IC = {
    lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
    unlock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-2"/></svg>',
    server: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
    site: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    api: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/></svg>',
    note: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
    eye: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>',
    user: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    face: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M9 9v1M15 9v1M12 9v4h-1M9 15.5a4 4 0 0 0 6 0"/></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    group: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c1-3.5 3.5-5 6.5-5s5.5 1.5 6.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14c2.5 0 4 1.5 4.5 4"/></svg>',
    more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/></svg>',
    paper: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/></svg>',
    dice: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    key: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/></svg>',
  };

  // ---------- общие мелочи ----------

  function app() {
    return window.LeoApp || {};
  }
  function K() {
    return (window.LeoOrgKb && window.LeoOrgKb._) || null;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function toast(m) {
    if (app().toast) app().toast(m);
  }
  function errText(e) {
    return (e && (e.detail || e.message)) || "Не получилось — попробуйте ещё раз";
  }
  function api(path, opts) {
    return app().apiFetch("/vaults" + path, opts || { method: "GET" });
  }
  function json(method, body) {
    return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  }
  function count(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    var w = m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
    return n + " " + w;
  }
  function when(iso) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var d = new Date(t);
    var now = new Date();
    var hm = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "сегодня " + hm;
    var y = new Date(now.getTime() - 86400000);
    if (d.toDateString() === y.toDateString()) return "вчера " + hm;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  }
  function whenFull(iso) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var d = new Date(t);
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) + " в " + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  function inTelegram() {
    var tg = window.Telegram && window.Telegram.WebApp;
    return !!(tg && tg.initData);
  }
  // Как называется вход по биометрии на этом устройстве (iPad притворяется Mac — отличаем по касаниям)
  var BIO = (function () {
    var ua = navigator.userAgent || "";
    if (/Macintosh|Mac OS X/.test(ua) && !/iPhone|iPad/.test(ua) && !(navigator.maxTouchPoints > 1)) return "Touch ID";
    if (/Windows/.test(ua)) return "Windows Hello";
    return "Face ID";
  })();
  function passkeysSupported() {
    return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
  }
  function unlocked() {
    return !!st.priv;
  }
  function vaultOf(id) {
    for (var i = 0; i < st.vaults.length; i++) if (st.vaults[i].id === id) return st.vaults[i];
    return null;
  }
  function itemOf(vid, iid) {
    var list = st.items[vid] || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === iid) return list[i];
    return null;
  }
  function canEdit(v) {
    return v && (RANK[v.role] || 0) >= RANK.editor;
  }
  function typeOf(it) {
    return TYPES[(it && it.data && it.data.type) || "site"] || TYPES.site;
  }
  function icoOf(it) {
    return IC[(it && it.data && it.data.type) || "site"] || IC.site;
  }

  // ---------- загрузка ----------

  async function load(force) {
    if (st.loading && !force) return st.loading;
    st.loading = (async function () {
      try {
        var r = await api("");
        st.enabled = r.enabled !== false;
        st.keys = r.keys || null;
        st.vaults = r.vaults || [];
        st.ownerKeys = r.org_owner_keys || {};
        st.loaded = true;
        if (unlocked()) await afterOpen();
        openFromLink(location.href);
      } catch (e) {
        st.loaded = true;
        st.enabled = false;
      }
      st.loading = null;
      refresh();
    })();
    return st.loading;
  }

  // Ссылка из уведомления: ...?vault=ID — открыть этот сейф (один раз на ссылку).
  var linkSeen = "";
  function openFromLink(href) {
    var id = 0;
    try {
      id = Number(new URL(href, location.href).searchParams.get("vault") || 0);
    } catch (_) {}
    if (!id || linkSeen === href || !vaultOf(id)) return;
    linkSeen = href;
    try {
      var u = new URL(location.href);
      u.searchParams.delete("vault");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (_) {}
    if (app().setTab) app().setTab("knowledge");
    setTimeout(function () { show(id); }, 50);
  }
  if (navigator.serviceWorker && navigator.serviceWorker.addEventListener) {
    navigator.serviceWorker.addEventListener("message", function (ev) {
      var d = ev && ev.data;
      if (d && d.type === "leo-open-url" && d.url && String(d.url).indexOf("vault=") >= 0) {
        load(true).then(function () { openFromLink(d.url); });
      }
    });
  }

  function applyState(r) {
    if (r && r.keys) st.keys = r.keys;
    if (r && r.vaults) st.vaults = r.vaults;
    if (r && r.org_owner_keys) st.ownerKeys = r.org_owner_keys;
  }

  function refresh() {
    var d = window.LeoOrgKbDesk;
    if (d && d.render) d.render();
    paintMobile();
    if (window.LeoOrgKb && window.LeoOrgKb.renderVaults) window.LeoOrgKb.renderVaults();
  }

  // ---------- открыть и закрыть ----------

  function touch() {
    if (unlocked()) st.lastActive = Date.now();
  }

  function lock(silent) {
    st.priv = null;
    if (st._codeUmk) st._codeUmk.fill(0);
    st._codeUmk = null;
    st.vk = {};
    st.vkVer = {};
    st.events = {};
    st.items = {};
    st.reveal = {};
    st.log = {};
    st.draft = null;
    if (st.view === "record" || st.view === "edit" || st.view === "list") st.view = "list";
    if (!silent) toast("Сейфы закрыты");
    refresh();
  }

  setInterval(function () {
    if (!unlocked()) return;
    if (Date.now() - st.lastActive > IDLE_MS) {
      lock(true);
      toast("Сейфы закрылись — 15 минут без действий");
      return;
    }
    var t = document.querySelectorAll("[data-vault-timer]");
    for (var i = 0; i < t.length; i++) t[i].textContent = timerText();
  }, 20000);
  ["pointerdown", "keydown"].forEach(function (ev) {
    document.addEventListener(ev, touch, true);
  });

  function timerText() {
    var left = Math.max(1, Math.ceil((IDLE_MS - (Date.now() - st.lastActive)) / 60000));
    return "закроется через " + left + " мин";
  }

  async function openWithUmk(umk) {
    st.priv = await CR.openPrivate(umk, st.keys.enc_private);
    umk.fill(0);
    st.lastActive = Date.now();
    await afterOpen();
  }

  // Ключи сейфов из конвертов + выдать ключ тем, кто ждёт (сервер ключа не знает — это делает браузер участника).
  async function afterOpen() {
    for (var i = 0; i < st.vaults.length; i++) {
      var v = st.vaults[i];
      if (!v.envelope) continue;
      if (st.vk[v.id] && st.vkVer[v.id] === v.key_version) continue;
      try {
        st.vk[v.id] = await CR.openEnvelope(v.envelope, st.priv, st.keys.public_key);
        st.vkVer[v.id] = v.key_version;
        delete st.items[v.id]; // ключ мог смениться — записи расшифруем заново
      } catch (_) {
        delete st.vk[v.id];
        v.broken = true; // конверт не открылся: ключи сменились (вход «с нуля») — ждём нового конверта
      }
    }
    for (var k = 0; k < st.vaults.length; k++) if (st.vaults[k].rotate) await rotateVault(st.vaults[k]);
    for (var j = 0; j < st.vaults.length; j++) await distribute(st.vaults[j]);
  }

  // Конверты тем, кто ждёт ключ (новые участники и владельцы организации).
  async function distribute(v) {
    if (!v || !st.vk[v.id]) return;
    var vk = st.vk[v.id];
    if (v.pending && v.pending.length) {
      try {
        var envs = [];
        for (var i = 0; i < v.pending.length; i++) {
          envs.push({ user_id: v.pending[i].user_id, envelope: await CR.sealEnvelope(vk, v.pending[i].public_key) });
        }
        var r = await api("/" + v.id + "/envelopes", json("POST", { envelopes: envs, key_version: st.vkVer[v.id] }));
        if (r && r.vault) replaceVault(r.vault);
        if (r && r.put) toast(count(r.put, "коллега получил", "коллеги получили", "коллег получили") + " доступ к сейфу «" + v.name + "»");
      } catch (_) {}
    }
  }

  // Новый ключ сейфа после ухода участника: старый мог остаться у него. Всё делает браузер:
  // расшифровать записи старым ключом, зашифровать новым, разложить новые конверты. Сервер ключей не видит.
  async function rotateVault(v) {
    if (!v || !v.rotate || !st.vk[v.id] || st.rotating[v.id]) return;
    st.rotating[v.id] = true;
    try {
      var old = st.vk[v.id];
      var list = (await api("/" + v.id + "/items")).items || [];
      var nvk = CR.newVaultKey();
      var items = [];
      for (var i = 0; i < list.length; i++) {
        // не расшифровалось — не трогаем сейф, чтобы ничего не потерять
        var data = await CR.openItem(old, v.id, list[i].blob);
        items.push({ id: list[i].id, blob: await CR.sealItem(nvk, v.id, data) });
      }
      var envs = [];
      for (var j = 0; j < v.rotate_keys.length; j++) {
        envs.push({ user_id: v.rotate_keys[j].user_id, envelope: await CR.sealEnvelope(nvk, v.rotate_keys[j].public_key) });
      }
      var r = await api("/" + v.id + "/rotate", json("POST", { key_version: v.key_version, items: items, envelopes: envs }));
      replaceVault(r.vault);
      st.vk[v.id] = nvk;
      st.vkVer[v.id] = r.vault.key_version;
      delete st.items[v.id];
      toast("Ключ сейфа «" + v.name + "» сменён — у ушедших его больше нет");
    } catch (e) {
      console.warn("[vault] rotate", v.id, e && e.name); // попробуем при следующем открытии
    }
    st.rotating[v.id] = false;
  }

  function replaceVault(nv) {
    for (var i = 0; i < st.vaults.length; i++) {
      if (st.vaults[i].id === nv.id) {
        st.vaults[i] = nv;
        return;
      }
    }
    st.vaults.push(nv);
  }

  // ---------- passkey ----------

  async function passkeyCreate() {
    var salt = CR.newPrfSalt();
    var saltBytes = CR.unb64u(salt);
    var cred = await navigator.credentials.create({
      publicKey: {
        rp: { name: "Орбита", id: location.hostname },
        user: { id: CR.rand(16), name: "Сейфы Орбиты", displayName: "Сейфы Орбиты" },
        challenge: CR.rand(32),
        pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
        authenticatorSelection: { residentKey: "required", userVerification: "required" },
        timeout: 120000,
        extensions: { prf: { eval: { first: saltBytes } } },
      },
    });
    var ext = cred.getClientExtensionResults ? cred.getClientExtensionResults() : {};
    if (!ext.prf || ext.prf.enabled === false) throw new Error("prf");
    var out = ext.prf.results && ext.prf.results.first;
    if (!out) {
      // Chrome отдаёт PRF только при входе — спрашиваем ключ ещё раз
      var a = await navigator.credentials.get({
        publicKey: {
          challenge: CR.rand(32),
          rpId: location.hostname,
          allowCredentials: [{ type: "public-key", id: cred.rawId }],
          userVerification: "required",
          timeout: 120000,
          extensions: { prf: { eval: { first: saltBytes } } },
        },
      });
      var e2 = a.getClientExtensionResults ? a.getClientExtensionResults() : {};
      out = e2.prf && e2.prf.results && e2.prf.results.first;
    }
    if (!out) throw new Error("prf");
    return { credentialId: CR.b64u(cred.rawId), salt: salt, prf: new Uint8Array(out) };
  }

  async function passkeyGet() {
    var pks = ((st.keys && st.keys.unlockers) || []).filter(function (u) { return u.kind === "passkey"; });
    if (!pks.length) throw new Error("nopasskey");
    var byCred = {};
    pks.forEach(function (u) {
      byCred[u.credential_id] = { first: CR.unb64u(u.salt) };
    });
    var a = await navigator.credentials.get({
      publicKey: {
        challenge: CR.rand(32),
        rpId: location.hostname,
        allowCredentials: pks.map(function (u) { return { type: "public-key", id: CR.unb64u(u.credential_id) }; }),
        userVerification: "required",
        timeout: 120000,
        extensions: { prf: { evalByCredential: byCred } },
      },
    });
    var id = CR.b64u(a.rawId);
    var u = pks.filter(function (x) { return x.credential_id === id; })[0];
    var ext = a.getClientExtensionResults ? a.getClientExtensionResults() : {};
    var out = ext.prf && ext.prf.results && ext.prf.results.first;
    if (!u || !out) throw new Error("prf");
    return { unlocker: u, prf: new Uint8Array(out) };
  }

  function deviceLabel() {
    var ua = navigator.userAgent || "";
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua)) return "iPad";
    if (/Android/.test(ua)) return "Android";
    if (/Mac/.test(ua)) return "Mac";
    if (/Windows/.test(ua)) return "Windows";
    return "Устройство";
  }

  function passkeyError(e) {
    var m = String((e && e.message) || "");
    if (e && e.name === "NotAllowedError") return "Отменено. Попробуйте ещё раз — или войдите кодом с аварийного листа.";
    if (m === "prf") return "Это устройство или браузер не умеет открывать сейфы по " + BIO + " (нужен Safari 18 и новее — macOS 15, iOS 18 — или свежий Chrome; в Arc не работает). Используйте код с аварийного листа.";
    if (m === "nopasskey") return "Для сейфов ещё нет ни одного устройства с " + BIO + " — войдите кодом с аварийного листа.";
    if (m === "unwrap") return BIO + " прошёл, но ключ сейфов не подошёл. Войдите кодом с аварийного листа и добавьте " + BIO + " заново.";
    if (m === "relogin") return "Войдите кодом с аварийного листа ещё раз — потом добавьте " + BIO + ".";
    // название ошибки браузера — чтобы по скриншоту понять причину (секретов в нём нет)
    return BIO + " не сработал (" + ((e && e.name) || "ошибка") + (m && m.length < 80 ? ": " + m : "") + "). Попробуйте ещё раз или войдите кодом с аварийного листа.";
  }

  // ---------- действия: включение ----------

  async function startSetup() {
    if (st.busy) return;
    st.busy = true;
    paint();
    try {
      var id = await CR.newIdentity();
      var passkey = null;
      var note = "";
      if (passkeysSupported()) {
        try {
          var pk = await passkeyCreate();
          passkey = {
            kind: "passkey", credential_id: pk.credentialId, salt: pk.salt, label: deviceLabel(),
            wrapped_umk: await CR.wrapUmkPrf(id.umk, pk.prf, pk.salt),
          };
        } catch (e) {
          note = (e && e.name === "NotAllowedError" ? "" + BIO + " не настроен" : passkeyError(e).split(".")[0]) +
            ". Пока сейфы будут открываться кодом с этого листа — " + BIO + " можно добавить при следующем входе.";
        }
      } else {
        note = "Этот браузер не умеет " + BIO + " для сейфов — открывать их можно кодом с аварийного листа.";
      }
      var code = CR.newRecoveryCode();
      var rec = await CR.wrapUmkRecovery(id.umk, code);
      st.setup = { identity: id, code: code, passkey: passkey, recovery: { kind: "recovery", salt: rec.salt, wrapped_umk: rec.wrapped }, note: note };
      st.view = "code";
    } catch (e) {
      toast(errText(e));
    }
    st.busy = false;
    paint();
  }

  async function finishSetup() {
    var s = st.setup;
    if (!s || st.busy) return;
    st.busy = true;
    paint();
    try {
      var unlockers = [s.recovery];
      if (s.passkey) unlockers.unshift(s.passkey);
      var r = await api("/keys", json("POST", {
        public_key: s.identity.publicKey, enc_private: s.identity.encPrivate, unlockers: unlockers, reset: !!(st.keys && st.keys.enabled),
      }));
      applyState(r);
      st.setup = null;
      await openWithUmk(s.identity.umk);
      st.view = "list";
      toast("Сейфы включены");
      st.busy = false;
      refresh();
      await runAfter();
    } catch (e) {
      toast(errText(e));
    }
    st.busy = false;
    refresh();
  }

  // ---------- действия: открыть ----------

  async function unlockPasskey() {
    if (st.busy) return;
    st.busy = true;
    paint();
    try {
      var r = await passkeyGet();
      var umk;
      try {
        umk = await CR.unwrapUmkPrf(r.unlocker.wrapped_umk, r.prf, r.unlocker.salt);
      } catch (_) {
        throw new Error("unwrap");
      }
      await openWithUmk(umk);
      api("/keys/unlockers/" + r.unlocker.id + "/used", json("POST", {})).catch(function () {});
      st.view = "list";
      await runAfter();
    } catch (e) {
      toast(passkeyError(e));
      st.view = "device";
    }
    st.busy = false;
    refresh();
  }

  async function unlockCode(code) {
    if (st.busy) return;
    var u = ((st.keys && st.keys.unlockers) || []).filter(function (x) { return x.kind === "recovery"; })[0];
    if (!u) return toast("Аварийного кода нет — войдите по " + BIO + "");
    if (!CR.recoveryLooksValid(code)) return toast("Код — 24 знака: латинские буквы и цифры, как на листе");
    st.busy = true;
    paint();
    try {
      var umk = await CR.unwrapUmkRecovery(u.wrapped_umk, code, u.salt);
      st._codeUmk = umk.slice(); // понадобится, если тут же добавят Face ID этого устройства
      await openWithUmk(umk);
      st.view = passkeysSupported() && !hasPasskeyHere() ? "add-device" : "list";
      await runAfter();
    } catch (e) {
      toast("Код не подошёл. Проверьте буквы и цифры — регистр и дефисы не важны.");
    }
    st.busy = false;
    refresh();
  }

  function hasPasskeyHere() {
    return false; // браузер не говорит, есть ли passkey на этом устройстве, — предлагаем добавить, можно пропустить
  }

  // Новое устройство: после входа кодом — завести Face ID здесь (та же UMK, новая обёртка).
  async function addThisDevice() {
    if (st.busy) return;
    st.busy = true;
    paint();
    try {
      if (!st._codeUmk) throw new Error("relogin");
      var pk = await passkeyCreate();
      var umk = st._codeUmk.slice(); // код держим до успеха: отмена или сбой — можно попробовать ещё раз
      var r = await api("/keys/unlockers", json("POST", {
        kind: "passkey", credential_id: pk.credentialId, salt: pk.salt, label: deviceLabel(),
        wrapped_umk: await CR.wrapUmkPrf(umk, pk.prf, pk.salt),
      }));
      umk.fill(0);
      st._codeUmk.fill(0);
      st._codeUmk = null;
      applyState(r);
      toast("Готово — здесь сейфы открываются по " + BIO + "");
      st.view = "list";
    } catch (e) {
      toast(passkeyError(e));
    }
    st.busy = false;
    refresh();
  }

  async function runAfter() {
    var f = st.after;
    st.after = null;
    if (f) await f();
  }

  function needOpen(then) {
    st.after = then || null;
    if (!st.keys || !st.keys.enabled) st.view = "setup";
    else st.view = "locked";
    show(st.vaultId);
  }

  // ---------- действия: сейфы ----------

  async function createVault(orgId) {
    if (!unlocked()) return needOpen(function () { return createVault(orgId); });
    var personal = orgId == null;
    var name = personal ? "Личный сейф" : await (app().promptDialog ? app().promptDialog("Название сейфа", "") : Promise.resolve(window.prompt("Название сейфа", "")));
    if (!name || !String(name).trim()) return;
    try {
      var vk = CR.newVaultKey();
      var env = await CR.sealEnvelope(vk, st.keys.public_key);
      // владельцы организации — владельцы всех её сейфов: конверт им сразу, чтобы сейф не потерялся, если создатель уйдёт
      var ownerEnvs = [];
      var owners = personal ? [] : (st.ownerKeys && st.ownerKeys[String(orgId)]) || [];
      for (var i = 0; i < owners.length; i++) {
        ownerEnvs.push({ user_id: owners[i].user_id, envelope: await CR.sealEnvelope(vk, owners[i].public_key) });
      }
      var r = await api("", json("POST", { org_id: personal ? null : orgId, name: String(name).trim(), envelope: env, owner_envelopes: ownerEnvs }));
      applyState(r);
      st.vk[r.created.id] = vk;
      st.vkVer[r.created.id] = r.created.key_version;
      st.items[r.created.id] = [];
      show(r.created.id);
      if (!personal) openAccess(r.created.id);
    } catch (e) {
      toast(errText(e));
    }
  }

  async function loadItems(v) {
    if (!st.vk[v.id] || st.items[v.id]) return;
    st.items[v.id] = "loading";
    try {
      var r = await api("/" + v.id + "/items");
      var out = [];
      for (var i = 0; i < r.items.length; i++) {
        var it = r.items[i];
        try {
          out.push({ id: it.id, updated_at: it.updated_at, updated_by_name: it.updated_by_name, data: await CR.openItem(st.vk[v.id], v.id, it.blob) });
        } catch (_) {
          out.push({ id: it.id, updated_at: it.updated_at, updated_by_name: it.updated_by_name, data: { type: "note", title: "Не удалось расшифровать" }, broken: true });
        }
      }
      st.items[v.id] = out;
    } catch (e) {
      st.items[v.id] = null;
      toast(errText(e));
    }
    paint();
  }

  async function saveItem() {
    var v = vaultOf(st.vaultId);
    var d = st.draft;
    if (!v || !d || st.busy) return;
    var form = container() && container().querySelector(".vlt-form");
    if (form) readForm(form);
    if (!String(d.data.title || "").trim()) return toast("Введите название записи");
    st.busy = true;
    try {
      var data = {};
      Object.keys(d.data).forEach(function (k) {
        var val = d.data[k];
        if (val != null && String(val) !== "") data[k] = typeof val === "string" ? val.trim() : val;
      });
      var blob = await CR.sealItem(st.vk[v.id], v.id, data);
      var body = { blob: blob, key_version: st.vkVer[v.id] };
      var r = d.id
        ? await api("/" + v.id + "/items/" + d.id, json("PUT", body))
        : await api("/" + v.id + "/items", json("POST", body));
      var list = st.items[v.id] || [];
      var cur = itemOf(v.id, r.id);
      if (cur) {
        cur.data = data;
        cur.updated_at = r.updated_at;
      } else {
        list.push({ id: r.id, data: data, updated_at: r.updated_at, updated_by_name: "" });
        st.items[v.id] = list;
        v.items = list.length;
      }
      delete st.log[r.id];
      st.draft = null;
      st.itemId = r.id;
      st.view = "record";
      toast("Сохранено");
      if (v.departures && v.departures.length) load(true); // сменённый пароль уходит из списка «смените пароли»
    } catch (e) {
      toast(errText(e));
      if (/сменился/.test(errText(e))) load(true); // ключ сменили — откроем заново, черновик остаётся
    }
    st.busy = false;
    refresh();
  }

  async function deleteItem(iid) {
    var v = vaultOf(st.vaultId);
    var it = v && itemOf(v.id, iid);
    if (!it) return;
    var ok = app().confirm ? await app().confirm("Удалить запись «" + (it.data.title || "") + "»? Вернуть её будет нельзя.") : window.confirm("Удалить запись?");
    if (!ok) return;
    try {
      await api("/" + v.id + "/items/" + iid, { method: "DELETE" });
      st.items[v.id] = (st.items[v.id] || []).filter(function (x) { return x.id !== iid; });
      v.items = st.items[v.id].length;
      st.view = "list";
      st.itemId = null;
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  async function copyField(iid, field) {
    var v = vaultOf(st.vaultId);
    var it = v && itemOf(v.id, iid);
    var val = it && it.data[field];
    if (!val) return;
    try {
      await navigator.clipboard.writeText(String(val));
    } catch (_) {
      return toast("Браузер не дал скопировать — выделите и скопируйте вручную");
    }
    var secret = field === "password" || (field === "note" && it.data.type === "note");
    if (secret) {
      toast("Скопировано · очистится через 30 с");
      setTimeout(function () {
        navigator.clipboard.writeText(" ").catch(function () {});
      }, CLIP_MS);
      api("/" + v.id + "/items/" + iid + "/log", json("POST", { action: "copy" })).catch(function () {});
      delete st.log[iid];
    } else {
      toast("Скопировано");
    }
  }

  function toggleReveal(iid) {
    var v = vaultOf(st.vaultId);
    st.reveal[iid] = !st.reveal[iid];
    if (st.reveal[iid] && v) {
      api("/" + v.id + "/items/" + iid + "/log", json("POST", { action: "reveal" })).catch(function () {});
      delete st.log[iid];
      setTimeout(function () {
        if (st.reveal[iid]) {
          st.reveal[iid] = false;
          paint();
        }
      }, CLIP_MS);
    }
    paint();
  }

  async function loadLog(v, iid) {
    if (st.log[iid]) return;
    st.log[iid] = "loading";
    try {
      st.log[iid] = (await api("/" + v.id + "/items/" + iid + "/log")).items || [];
    } catch (_) {
      st.log[iid] = [];
    }
    paint();
  }

  // ---------- уход участника и аварийный доступ ----------

  async function dismissDepartures(vid) {
    try {
      replaceVault((await api("/" + vid + "/departures/dismiss", json("POST", {}))).vault);
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  async function requestAccess(vid) {
    try {
      applyState(await api("/" + vid + "/request", json("POST", {})));
      toast("Запрос отправлен владельцам сейфа");
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  async function cancelRequest(vid) {
    try {
      applyState(await api("/" + vid + "/request/cancel", json("POST", {})));
      toast("Запрос отменён");
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  // Владелец сейфа отвечает на запрос: роль — открыть, пусто — отклонить. Ключ новому участнику кладёт этот браузер.
  async function decideRequest(vid, uid, role) {
    try {
      var r = await api("/" + vid + "/request/decide", json("POST", { user_id: uid, role: role }));
      replaceVault(r.vault);
      delete st.events[vid];
      var v = vaultOf(vid);
      if (role && v) {
        await distribute(v);
        toast(unlocked() ? "Доступ открыт" : "Доступ открыт — ключ получит, когда вы откроете сейфы");
      } else {
        toast("Запрос отклонён");
      }
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  async function loadEvents(v) {
    if (st.events[v.id]) return;
    st.events[v.id] = "loading";
    try {
      st.events[v.id] = (await api("/" + v.id + "/events")).items || [];
    } catch (_) {
      st.events[v.id] = [];
    }
    paint();
  }

  // ---------- доступ ----------

  function sheetFn() {
    var k = K();
    return k && k.sheet;
  }

  async function openAccess(vid) {
    var v = vaultOf(vid);
    if (!v || v.personal) return;
    var kb = K() && K().kbOf(v.org_id);
    var mk = sheetFn();
    if (!mk || !kb) return;
    var data;
    try {
      data = await api("/" + vid + "/access");
    } catch (e) {
      return toast(errText(e));
    }
    var sh = mk("Доступ к сейфу", "");
    var owner = data.role === "owner";

    function roleSel(value, attrs) {
      return '<select class="pf-select kb-role" ' + attrs + ">" + ["viewer", "editor", "owner"].map(function (r) {
        return '<option value="' + r + '"' + (r === value ? " selected" : "") + ">" + ROLE_LABEL[r] + "</option>";
      }).join("") + "</select>";
    }
    function stateLabel(p) {
      if (p.state === "ok") return "";
      if (p.state === "pending") return '<span class="vlt-pill">ждёт ключ</span>';
      return '<span class="vlt-pill">не включил сейфы</span>';
    }
    function paintSheet() {
      var taken = data.acl.map(function (a) { return a.principal; });
      var html = '<p class="kb-crumbs">Сейф <b>«' + esc(v.name) + "»</b></p>";
      html += '<div class="pf-group"><p class="pf-group-title">Кому открыт</p><div class="pf-list">';
      data.acl.forEach(function (a) {
        var name = K().principalName(kb, a.principal);
        var sub = K().principalSub(kb, a.principal);
        var right = owner
          ? roleSel(a.role, 'data-va="role" data-p="' + esc(a.principal) + '"') +
            '<button type="button" class="pf-del" data-va="del" data-p="' + esc(a.principal) + '" aria-label="Убрать" title="Убрать">✕</button>'
          : '<span class="pf-row-val">' + esc(ROLE_LABEL[a.role] || a.role) + "</span>";
        html += '<div class="pf-row pf-row--static"><span class="pf-avatar" aria-hidden="true">' + esc(K().initials(name)) + "</span>" +
          '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(name) + "</span>" +
          (sub ? '<span class="pf-row-sub">' + esc(sub) + "</span>" : "") + "</span>" + right + "</span></div>";
      });
      if (owner) {
        var opts = '<option value="">Открыть: отделу, команде, человеку…</option>';
        var groups = "";
        (kb.groups || []).forEach(function (g) {
          if (taken.indexOf("g:" + g.id) < 0) groups += '<option value="g:' + g.id + '">' + esc(g.name) + "</option>";
          (g.teams || []).forEach(function (t) {
            if (taken.indexOf("g:" + t.id) < 0) groups += '<option value="g:' + t.id + '">' + esc(t.name) + "</option>";
          });
        });
        if (groups) opts += '<optgroup label="Отделы и команды">' + groups + "</optgroup>";
        var people = "";
        (kb.members || []).forEach(function (m) {
          if (taken.indexOf("u:" + m.user_id) < 0) people += '<option value="u:' + esc(m.user_id) + '">' + esc(m.name) + (m.guest ? " (гость)" : "") + "</option>";
        });
        if (people) opts += '<optgroup label="Люди">' + people + "</optgroup>";
        html += '<div class="pf-row pf-row--static pf-add-select"><span class="pf-ico pf-add-ico" aria-hidden="true">' + IC.plus + "</span>" +
          '<span class="pf-row-body org-add-row kb-add-row"><select class="pf-input" data-va="who" aria-label="Кому открыть">' + opts + "</select>" +
          roleSel("viewer", 'data-va="new-role" aria-label="Роль"') + "</span></div>";
      }
      html += "</div>";
      html += '<p class="pf-hint">Смотрит — открывает и копирует. Правит — ещё добавляет и меняет записи. Владелец — ещё меняет доступ. ' +
        "Владельцы организации — владельцы всех её сейфов. Остальные видят название сейфа и могут запросить доступ. Открыть сейф всем сразу нельзя.</p></div>";
      if (data.people && data.people.length) {
        html += '<div class="pf-group"><p class="pf-group-title">Люди · ' + data.people.length + '</p><div class="pf-list">';
        data.people.forEach(function (p) {
          html += '<div class="pf-row pf-row--static"><span class="pf-avatar" aria-hidden="true">' + esc(K().initials(p.name)) + "</span>" +
            '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(p.name) + "</span>" +
            (p.org_owner ? '<span class="pf-row-sub">владелец организации — владелец всех её сейфов</span>' : "") + "</span>" + stateLabel(p) + "</span></div>";
        });
        html += "</div>";
        var waiting = data.people.filter(function (p) { return p.state !== "ok"; }).length;
        if (waiting) {
          html += '<p class="pf-hint">«Ждёт ключ» — получит доступ, как только кто-то из участников откроет сейфы. ' +
            "«Не включил сейфы» — получит после того, как включит их у себя.</p>";
        }
        html += "</div>";
      }
      sh.body.innerHTML = html;
    }

    async function put(principal, role) {
      try {
        var r = await api("/" + vid + "/access", json("PUT", { principal: principal, role: role }));
        data = r;
        if (r.vault) {
          replaceVault(r.vault);
          v = vaultOf(vid);
          await distribute(v);
          if (r.vault.pending && r.vault.pending.length && !unlocked()) {
            toast("Ключ получат, когда вы или коллеги откроете сейфы");
          }
          data = await api("/" + vid + "/access");
        }
        paintSheet();
        refresh();
      } catch (e) {
        toast(errText(e));
        paintSheet();
      }
    }

    sh.body.addEventListener("change", function (e) {
      var t = e.target;
      var a = t.getAttribute("data-va");
      if (a === "role") put(t.getAttribute("data-p"), t.value);
      if (a === "who" && t.value) {
        var r = sh.body.querySelector('[data-va="new-role"]');
        put(t.value, r ? r.value : "viewer");
      }
    });
    sh.body.addEventListener("click", function (e) {
      var b = e.target.closest('[data-va="del"]');
      if (b) put(b.getAttribute("data-p"), "");
    });
    paintSheet();
  }

  async function renameVault(vid) {
    var v = vaultOf(vid);
    if (!v) return;
    var name = app().promptDialog ? await app().promptDialog("Название сейфа", v.name) : window.prompt("Название сейфа", v.name);
    if (!name || name.trim() === v.name) return;
    try {
      applyState(await api("/" + vid, json("PATCH", { name: name.trim() })));
      refresh();
    } catch (e) {
      toast(errText(e));
    }
  }

  async function deleteVault(vid) {
    var v = vaultOf(vid);
    if (!v) return;
    var ok = app().confirm
      ? await app().confirm("Удалить сейф «" + v.name + "» и все " + count(v.items || 0, "запись", "записи", "записей") + " в нём? Восстановить будет нельзя — ни нам, ни вам.")
      : window.confirm("Удалить сейф?");
    if (!ok) return;
    try {
      applyState(await api("/" + vid, { method: "DELETE" }));
      delete st.vk[vid];
      delete st.items[vid];
      st.vaultId = null;
      leave();
    } catch (e) {
      toast(errText(e));
    }
  }

  // ---------- навигация ----------

  // Показать сейф (или экран включения/открытия, если сейфы закрыты).
  function show(vid) {
    st.vaultId = vid == null ? null : Number(vid);
    var v = vaultOf(st.vaultId);
    if (!st.keys || !st.keys.enabled) {
      if (st.view !== "code") st.view = "setup";
    } else if (!unlocked()) {
      if (st.view !== "device") st.view = "locked";
    } else if (["setup", "code", "locked", "device"].indexOf(st.view) >= 0 || !v) {
      st.view = "list";
    }
    if (window.LeoOrgKbDesk && window.LeoOrgKbDesk.isOn && window.LeoOrgKbDesk.isOn()) {
      window.LeoOrgKbDesk.showVault(st.vaultId);
    } else {
      openMobile();
    }
  }

  function leave() {
    st.view = "list";
    st.itemId = null;
    st.draft = null;
    if (st.mobile) closeMobile();
    else if (window.LeoOrgKbDesk && window.LeoOrgKbDesk.goHome) window.LeoOrgKbDesk.goHome();
  }

  function container() {
    if (st.mobile) return st.mobile.querySelector(".vlt-mobile-body");
    var d = document.getElementById("kb-desk");
    return d && d.querySelector(".vlt-page") ? d : null;
  }

  function paint() {
    if (st.mobile) return paintMobile();
    var d = window.LeoOrgKbDesk;
    if (d && d.isOn && d.isOn() && d.inVault && d.inVault()) d.render();
  }

  // ---------- разметка ----------

  function bar(pathParts, actions) {
    var mobile = !!st.mobile;
    var path = mobile
      ? '<button type="button" class="vlt-back" data-v="back">' + IC.back + "<span>" + esc(pathParts.length > 1 ? pathParts[pathParts.length - 2][0] : "База знаний") + "</span></button>"
      : '<nav class="kb-path" aria-label="Путь"><button type="button" data-v="home">База знаний</button>' + pathParts.map(function (p, i) {
        var last = i === pathParts.length - 1;
        return '<span class="kb-path-sep">›</span>' + (last || !p[1]
          ? '<span class="' + (last ? "kb-path-cur" : "") + '"' + (last ? ' aria-current="page"' : "") + ">" + esc(p[0]) + "</span>"
          : '<button type="button" data-v="' + p[1] + '">' + esc(p[0]) + "</button>");
      }).join("") + "</nav>";
    var tree = !mobile && window.LeoOrgKbDesk && window.LeoOrgKbDesk.treeHidden && window.LeoOrgKbDesk.treeHidden()
      ? '<button type="button" class="kb-n-btn kb-bar-tree" data-p="show-tree" aria-label="Показать дерево" title="Показать дерево"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg></button>'
      : "";
    return '<div class="kb-bar">' + tree + path + '<span class="kb-bar-actions">' + (actions || "") + "</span></div>";
  }
  function btn(label, attrs, cls, ico) {
    return '<button type="button" class="kb-btn ' + (cls || "") + '" ' + attrs + ">" + (ico || "") + "<span>" + esc(label) + "</span></button>";
  }
  function timer() {
    return unlocked() ? '<span class="vlt-timer">' + IC.clock + '<span data-vault-timer>' + esc(timerText()) + "</span></span>" +
      btn("Закрыть", 'data-v="lock"', "kb-btn--ghost", IC.lock) : "";
  }
  function crumbs(v) {
    var parts = [["Сейфы", null]];
    if (v) parts.push([v.name, st.view === "list" ? null : "list"]);
    return parts;
  }
  function busyAttr() {
    return st.busy ? " disabled" : "";
  }

  function pageHtml() {
    if (!st.loaded) return bar([["Сейфы"]]) + '<div class="kb-page vlt-page"><p class="pf-hint">Загружаю…</p></div>';
    if (!st.enabled) return bar([["Сейфы"]]) + '<div class="kb-page vlt-page vlt-center"><p class="muted lead">Сейфы пока недоступны.</p></div>';
    if (inTelegram()) {
      return bar([["Сейфы"]]) + '<div class="kb-page vlt-page vlt-center"><div class="vlt-big">' + IC.lock + '</div><h1 class="screen-title kb-title">Сейфы — в браузере</h1>' +
        '<p class="muted lead">В Telegram сейфы не открываются: Face ID, Touch ID и шифрование работают только в браузере и приложении Орбиты.</p>' +
        btn("Открыть Орбиту в браузере", 'data-v="open-browser"', "kb-btn--primary vlt-btn-lg") + "</div>";
    }
    var v = vaultOf(st.vaultId);
    if (v && v.foreign && st.view !== "code") return foreignHtml(v); // сейф организации без доступа: можно запросить
    // экран всегда соответствует состоянию ключей: не включены → включить, закрыты → открыть
    if (!st.keys || !st.keys.enabled) {
      if (st.view !== "code") st.view = "setup";
    } else if (!unlocked() && ["locked", "device", "code"].indexOf(st.view) < 0) {
      st.view = "locked";
    }
    switch (st.view) {
      case "setup": return setupHtml();
      case "code": return codeHtml();
      case "locked": return lockedHtml(v);
      case "device": return deviceHtml(v);
      case "add-device": return addDeviceHtml();
      case "edit": return editHtml(v);
      case "record": return recordHtml(v);
      default: return listHtml(v);
    }
  }

  function setupHtml() {
    return bar([["Сейфы"]]) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.lock + '</div><h1 class="screen-title kb-title">Включите сейфы</h1>' +
      '<p class="muted lead">Пароли и ключи шифруются на ваших устройствах — ни Орбита, ни Лео их не видят. Мастер-пароль придумывать не нужно.</p>' +
      '<div class="vlt-steps"><div class="vlt-step"><span>1</span><div><b>Face ID или Touch ID</b><p>Так вы будете открывать сейфы. Ключ сохранится в Связке ключей iCloud и заработает на других ваших устройствах Apple.</p></div></div>' +
      '<div class="vlt-step"><span>2</span><div><b>Аварийный лист</b><p>Код на случай, если потеряете все устройства. Его покажем один раз — распечатайте или перепишите.</p></div></div></div>' +
      '<div class="vlt-actions"><button type="button" class="kb-btn kb-btn--primary vlt-btn-lg" data-v="setup"' + busyAttr() + ">" + IC.face + "<span>" + (st.busy ? "Подождите…" : "Включить сейфы") + "</span></button></div>" +
      '<p class="pf-hint">Если потерять и устройства, и аварийный лист, сами записи не пропадут — коллеги по сейфу выдадут доступ заново.</p></div>';
  }

  function codeHtml() {
    var s = st.setup || {};
    var reset = st.keys && st.keys.enabled;
    return bar([["Сейфы"], ["Аварийный лист"]]) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.paper + '</div><h1 class="screen-title kb-title">Сохраните аварийный лист</h1>' +
      (s.note ? '<div class="kb-banner kb-banner--stale vlt-left"><span>' + esc(s.note) + "</span></div>" : "") +
      '<p class="muted lead">' + (s.passkey ? "" + BIO + " для сейфов настроен. " : "") + "Этот код откроет сейфы, если под рукой не будет ваших устройств. Больше мы его не покажем.</p>" +
      '<div class="vlt-paper" id="vlt-print"><span class="vlt-label">Код восстановления сейфов Орбиты</span><div class="vlt-code">' + esc(s.code || "") + "</div>" +
      '<span class="vlt-label">Храните на бумаге, вне компьютера. Не фотографируйте и не пересылайте.</span></div>' +
      '<div class="vlt-actions">' + btn("Распечатать", 'data-v="print"', "", IC.paper) + btn("Скопировать", 'data-v="copy-code"', "kb-btn--ghost", IC.copy) + "</div>" +
      '<label class="vlt-check"><input type="checkbox" data-v="saved-check" /> <span>Я сохранил код вне компьютера</span></label>' +
      '<div class="vlt-actions"><button type="button" class="kb-btn kb-btn--primary vlt-btn-lg" data-v="finish" disabled>' + (reset ? "Начать с новыми ключами" : "Готово") + "</button></div>" +
      (reset ? '<p class="pf-hint">Старые ключи перестанут работать: доступ к сейфам организации заново выдадут коллеги при следующем открытии.</p>' : "") +
      "</div>";
  }

  function lockedHtml(v) {
    var hasPk = ((st.keys && st.keys.unlockers) || []).some(function (u) { return u.kind === "passkey"; });
    return bar(crumbs(v)) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.face + '</div><h1 class="screen-title kb-title">' + (v ? "Сейф «" + esc(v.name) + "» закрыт" : "Сейфы закрыты") + "</h1>" +
      '<p class="muted lead">Подтвердите, что это вы. Сейфы закрываются сами через 15 минут без действий.</p>' +
      (v ? requestsHtml(v) : "") +
      (hasPk && passkeysSupported()
        ? '<div class="vlt-actions"><button type="button" class="kb-btn kb-btn--primary vlt-btn-lg" data-v="unlock"' + busyAttr() + ">" + IC.face + "<span>" + (st.busy ? "Подождите…" : "Открыть по " + BIO + "") + "</span></button></div>" +
          '<button type="button" class="pf-link" data-v="device">Другое устройство или нет ' + BIO + '</button>'
        : codeFormHtml() + '<p class="pf-hint vlt-reset"><button type="button" class="pf-link" data-v="reset">Потеряли аварийный лист?</button></p>') +
      "</div>";
  }

  function codeFormHtml() {
    return '<form class="vlt-codeform" data-v="code-form"><input class="pf-input vlt-code-input" name="code" placeholder="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="Код с аварийного листа" />' +
      '<button type="submit" class="kb-btn kb-btn--primary"' + busyAttr() + ">Открыть</button></form>" +
      '<p class="pf-hint">Код с аварийного листа. Регистр и дефисы не важны.</p>';
  }

  function deviceHtml(v) {
    return bar(crumbs(v).concat([["Другое устройство"]])) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.phone + '</div><h1 class="screen-title kb-title">Открыть на этом устройстве</h1>' +
      '<p class="muted lead">Если это iPhone, iPad или Mac на том же Apple ID — Face ID и Touch ID уже работают: ключ пришёл через iCloud. ' +
      "Иначе войдите один раз кодом с аварийного листа и добавьте " + BIO + " этого устройства.</p>" +
      '<div class="vlt-actions">' + btn("Попробовать " + BIO + "", 'data-v="unlock"', "", IC.face) + "</div>" +
      codeFormHtml() +
      '<p class="pf-hint vlt-reset"><button type="button" class="pf-link" data-v="reset">Потеряли и устройства, и аварийный лист?</button></p></div>';
  }

  async function resetKeys() {
    var ok = app().confirm
      ? await app().confirm("Начать с новыми ключами? Сейфы организации снова откроются, когда коллеги по сейфу откроют их у себя. " +
        "Сейфы, где вы единственный участник, и личный сейф открыть будет нельзя — их содержимое пропадёт.")
      : window.confirm("Начать с новыми ключами?");
    if (ok) startSetup();
  }

  function addDeviceHtml() {
    return bar([["Сейфы"], ["Это устройство"]]) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.face + '</div><h1 class="screen-title kb-title">Открывать здесь по ' + BIO + '?</h1>' +
      '<p class="muted lead">Тогда в следующий раз код с листа не понадобится.</p>' +
      '<div class="vlt-actions">' + btn("Не сейчас", 'data-v="skip-device"', "kb-btn--ghost") +
      '<button type="button" class="kb-btn kb-btn--primary vlt-btn-lg" data-v="add-device"' + busyAttr() + ">" + IC.face + "<span>Добавить " + BIO + "</span></button></div></div>";
  }

  function listHtml(v) {
    if (!v) return bar([["Сейфы"]], timer()) + '<div class="kb-page vlt-page"><p class="pf-hint">Выберите сейф слева.</p></div>';
    var items = st.items[v.id];
    if (st.vk[v.id] && !items) loadItems(v);
    var actions = timer() +
      (v.personal ? "" : btn("Доступ", 'data-v="access"', "kb-btn--ghost", IC.group)) +
      (v.role === "owner" ? '<button type="button" class="kb-n-btn kb-bar-more" data-v="vault-more" aria-label="Действия с сейфом" title="Действия с сейфом">' + IC.more + "</button>" : "") +
      (canEdit(v) && st.vk[v.id] ? btn("Запись", 'data-v="new"', "kb-btn--primary", IC.plus) : "");
    var html = bar(crumbs(v), actions) + '<div class="kb-page vlt-page">';
    html += '<h1 class="screen-title kb-title"><span class="vlt-title-ico">' + IC.lock + "</span>" + esc(v.name) + "</h1>";
    html += '<p class="muted lead lead-tight">' + (v.personal ? "Личный сейф — видите только вы" : "Открыт: " + esc(v.access) + " · вы — " + esc((ROLE_LABEL[v.role] || "").toLowerCase())) + "</p>";
    html += requestsHtml(v);
    if (!st.vk[v.id]) {
      if (!v.holders || (v.holders === 1 && v.broken)) {
        html += '<div class="kb-banner kb-banner--stale">' + IC.lock + "<span>Ключа этого сейфа больше нет ни у кого из участников — записи открыть нельзя. " +
          (v.role === "owner" ? "Удалите сейф через «⋯» и заведите новый." : "Попросите владельца завести новый сейф.") + "</span></div></div>";
        return html;
      }
      html += '<div class="kb-banner">' + IC.clock + "<span>" + (v.broken
        ? "Ключ этого сейфа выдан вашим старым ключам. Новый выдадут коллеги, как только откроют сейфы, — мы им напомним."
        : "Вас добавили в этот сейф. Доступ появится, как только кто-то из участников откроет сейфы.") + "</span></div></div>";
      return html;
    }
    if (items === "loading" || !items) return html + '<p class="pf-hint">Расшифровываю…</p></div>';
    html += departuresHtml(v, items);
    html += '<p class="kb-label">В сейфе · ' + items.length + "</p>";
    if (!items.length) {
      html += '<div class="kb-table"><div class="vlt-empty"><b>Пока пусто</b><span>Добавьте доступ к серверу, сайту или API-ключ.</span>' +
        (canEdit(v) ? btn("Запись", 'data-v="new"', "kb-btn--primary", IC.plus) : "") + "</div></div>";
    } else {
      html += '<div class="kb-table vlt-table"><div class="kb-row kb-row--head" aria-hidden="true"><span></span><span>Название</span><span>Логин</span><span>Изменена</span><span></span></div>';
      items.slice().sort(function (a, b) { return String(a.data.title || "").localeCompare(String(b.data.title || ""), "ru"); }).forEach(function (it) {
        var d = it.data;
        var sub = esc(typeOf(it).label + (d.address ? " · " + d.address : "")) + expiresHtml(d);
        html += '<div class="kb-row" role="button" tabindex="0" data-v="open" data-id="' + it.id + '">' +
          '<span class="kb-row-ico">' + icoOf(it) + '</span><span class="kb-row-title"><b>' + esc(d.title || "Без названия") + "</b><span>" + sub + "</span></span>" +
          '<span class="kb-row-c vlt-mono">' + esc(d.login || "") + '</span><span class="kb-row-c">' + esc(when(it.updated_at)) + "</span>" +
          '<span class="kb-row-c vlt-row-act">' +
          (d.login ? '<button type="button" class="vlt-ibtn" data-v="copy" data-id="' + it.id + '" data-f="login" title="Скопировать логин" aria-label="Скопировать логин">' + IC.user + "</button>" : "") +
          (d.password ? '<button type="button" class="vlt-ibtn" data-v="copy" data-id="' + it.id + '" data-f="password" title="Скопировать пароль" aria-label="Скопировать пароль">' + IC.copy + "</button>" : "") +
          "</span></div>";
      });
      if (canEdit(v)) html += '<div class="kb-table-add">' + btn("Запись", 'data-v="new"', "", IC.plus) + "</div>";
      html += "</div>";
    }
    html += eventsHtml(v);
    html += '<p class="pf-hint">Значок копирует пароль, буфер очистится через 30 секунд. Лео, поиск и доступ для ИИ содержимое сейфов не видят.</p>';
    return html + "</div>";
  }

  // Запросы доступа — владельцу сейфа (ответить можно и при закрытых сейфах; ключ положим при открытии).
  function requestsHtml(v) {
    var list = v.requests || [];
    if (!list.length) return "";
    return '<div class="vlt-deps vlt-reqs">' + list.map(function (r) {
      return '<div class="vlt-req"><span class="pf-avatar" aria-hidden="true">' + esc(initials(r.name)) + "</span><span class=\"vlt-req-text\"><b>" + esc(r.name) +
        "</b> просит доступ · " + esc(when(r.at)) + '</span><span class="vlt-req-acts">' +
        btn("Смотрит", 'data-v="req-ok" data-u="' + esc(r.user_id) + '" data-r="viewer"', "") +
        btn("Правит", 'data-v="req-ok" data-u="' + esc(r.user_id) + '" data-r="editor"', "") +
        btn("Отклонить", 'data-v="req-no" data-u="' + esc(r.user_id) + '"', "kb-btn--ghost") + "</span></div>";
    }).join("") + "</div>";
  }
  function initials(name) {
    var k = K();
    if (k && k.initials) return k.initials(name);
    return String(name || "?").trim().slice(0, 1).toUpperCase();
  }

  // После ухода участника: что он мог видеть и ещё не сменили.
  function departuresHtml(v, items) {
    var deps = v.departures || [];
    if (!deps.length || !canEdit(v)) return "";
    var since = deps[deps.length - 1].at;
    var stale = items.filter(function (it) { return String(it.updated_at || "") < since; });
    if (!stale.length) return "";
    var names = deps.map(function (d) { return d.name; }).join(", ");
    var html = '<div class="vlt-deps"><div class="vlt-deps-head">' + IC.alert + "<div><b>Смените пароли: " + (deps.length > 1 ? "из сейфа ушли " : "из сейфа ушёл(ушла) ") + esc(names) + "</b>" +
      "<p>" + (v.rotate ? "Ключ сейфа сменится, как только сейф откроет участник с правом правки." : "Ключ сейфа уже сменён — старым его не открыть.") +
      " Но пароли, которые " + (deps.length > 1 ? "они видели" : "он(а) видел(а)") + ", остаются рабочими — смените их в самих сервисах и здесь.</p></div></div>";
    html += '<div class="kb-lines">' + stale.map(function (it) {
      return '<div class="kb-line vlt-logline"><span class="kb-line-ico">' + icoOf(it) + '</span><span class="kb-line-text"><b>' + esc(it.data.title || "Без названия") + "</b><span>" +
        esc(typeOf(it).label) + "</span></span><span></span>" + btn("Сменить", 'data-v="edit" data-id="' + it.id + '"', "kb-btn--ghost") + "</div>";
    }).join("") + "</div>";
    html += '<div class="vlt-actions vlt-left">' + btn("Всё сменили — скрыть", 'data-v="dismiss-deps"', "kb-btn--ghost") + "</div></div>";
    return html;
  }

  function eventsHtml(v) {
    if (v.personal) return "";
    if (!st.events[v.id]) loadEvents(v);
    var ev = st.events[v.id];
    if (!ev || ev === "loading" || !ev.length) return "";
    var words = {
      access_request: "запросил(а) доступ", access_grant: "открыл(а) доступ по запросу", access_reject: "отклонил(а) запрос доступа",
    };
    return '<p class="kb-label">Журнал сейфа</p><div class="kb-lines">' + ev.slice(0, 8).map(function (l) {
      return '<div class="kb-line vlt-logline"><span class="kb-line-ico">' + IC.user + '</span><span class="kb-line-text"><b>' + esc(l.name || "Кто-то") + "</b><span>" +
        esc(words[l.action] || l.action) + '</span></span><span></span><span class="kb-line-right">' + esc(when(l.at)) + "</span></div>";
    }).join("") + "</div>";
  }

  // Сейф организации, куда человека не добавили: название и «Запросить доступ».
  function foreignHtml(v) {
    var r = v.request;
    var html = bar(crumbs(v)) + '<div class="kb-page vlt-page vlt-center">' +
      '<div class="vlt-big">' + IC.lock + '</div><h1 class="screen-title kb-title">Сейф «' + esc(v.name) + "»</h1>" +
      '<p class="muted lead">У вас нет доступа к этому сейфу. Открыт: ' + esc(v.access) + ".</p>";
    if (r && r.status === "pending") {
      html += '<div class="kb-banner vlt-left">' + IC.clock + "<span>Запрос отправлен " + esc(when(r.created_at)) +
        ". Владельцы сейфа получили уведомление — когда откроют доступ, мы напишем.</span></div>" +
        '<div class="vlt-actions">' + btn("Отменить запрос", 'data-v="req-cancel"', "kb-btn--ghost") + "</div>";
      return html + "</div>";
    }
    if (r && r.status === "rejected") {
      html += '<div class="kb-banner kb-banner--stale vlt-left">' + IC.alert + "<span>Запрос отклонил(а) " + esc(r.decided_by_name || "владелец сейфа") + " " + esc(when(r.decided_at)) +
        ". Если доступ нужен — напишите владельцу сейфа или запросите снова.</span></div>";
    }
    html += '<div class="vlt-actions">' + btn(r ? "Запросить снова" : "Запросить доступ", 'data-v="req"', "kb-btn--primary vlt-btn-lg", IC.key) + "</div>" +
      '<p class="pf-hint">Владельцы сейфа получат уведомление в Telegram, пушем и на почту и решат, открыть ли вам сейф.' +
      (!st.keys || !st.keys.enabled ? " Включите сейфы у себя заранее — без этого ключ сейфа вам не передать." : "") + "</p>";
    return html + "</div>";
  }

  // Ключ скоро истечёт — видно в списке, чтобы успели продлить
  function expiresHtml(d) {
    var t = Date.parse(d && d.expires ? d.expires + "T23:59:59" : "");
    if (!t) return "";
    var days = Math.ceil((t - Date.now()) / 86400000);
    if (days < 0) return ' · <span class="kb-st kb-st--warn"><i></i>истёк</span>';
    if (days <= 30) return ' · <span class="kb-st kb-st--warn"><i></i>истекает через ' + esc(count(days, "день", "дня", "дней")) + "</span>";
    return "";
  }

  function recordHtml(v) {
    var it = v && itemOf(v.id, st.itemId);
    if (!it) {
      st.view = "list";
      return listHtml(v);
    }
    var d = it.data;
    var t = typeOf(it);
    if (!st.log[it.id]) loadLog(v, it.id);
    var actions = timer() + (canEdit(v) ? btn("Изменить", 'data-v="edit" data-id="' + it.id + '"', "") : "");
    var html = bar(crumbs(v).concat([[d.title || "Запись"]]), actions) + '<div class="kb-page vlt-page vlt-record">';
    html += '<h1 class="screen-title kb-title"><span class="vlt-title-ico">' + icoOf(it) + "</span>" + esc(d.title || "Без названия") + "</h1>";
    html += '<p class="muted lead lead-tight">' + esc(t.label) + (it.updated_at ? " · изменена " + esc(when(it.updated_at)) + (it.updated_by_name ? ", " + esc(it.updated_by_name) : "") : "") + "</p>";
    html += '<div class="vlt-fields">';
    t.fields.forEach(function (f) {
      var key = f[0], kind = f[3] || "";
      var val = d[key];
      if (!val) return;
      var secret = kind === "secret" || kind === "text-secret";
      var shown = !secret || st.reveal[it.id];
      var shownVal = kind === "date" && /^\d{4}-\d{2}-\d{2}$/.test(String(val))
        ? new Date(val + "T12:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) + expiresHtml({ expires: val }).replace(/^ · /, " ")
        : esc(val);
      var body = shown ? shownVal : "••••••••••••••••";
      var isLink = key === "address" && /^https?:\/\//i.test(String(val));
      if (isLink && shown) body = '<a href="' + esc(val) + '" target="_blank" rel="noopener noreferrer">' + esc(val) + "</a>";
      html += '<div class="vlt-field"><span class="vlt-label">' + esc(f[1]) + '</span><span class="vlt-val' + (kind.indexOf("text") === 0 ? " vlt-val--text" : "") + '">' +
        '<span class="vlt-val-body">' + body + '</span><span class="vlt-acts">' +
        (secret ? '<button type="button" class="vlt-ibtn" data-v="reveal" data-id="' + it.id + '" title="' + (shown ? "Скрыть" : "Показать") + '" aria-label="' + (shown ? "Скрыть" : "Показать") + '">' + (shown ? IC.eyeOff : IC.eye) + "</button>" : "") +
        (kind === "date" ? "" : '<button type="button" class="vlt-ibtn" data-v="copy" data-id="' + it.id + '" data-f="' + key + '" title="Скопировать" aria-label="Скопировать">' + IC.copy + "</button>") +
        "</span></span></div>";
    });
    html += "</div>";
    var log = st.log[it.id];
    html += '<p class="kb-label">Кто открывал</p>';
    if (log === "loading" || !log) html += '<p class="pf-hint">Загружаю…</p>';
    else {
      var words = { copy: "скопировал(а) пароль", reveal: "открыл(а) пароль", create: "создал(а) запись", update: "изменил(а) запись" };
      html += '<div class="kb-lines">' + log.slice(0, 12).map(function (l) {
        return '<div class="kb-line vlt-logline"><span class="kb-line-ico">' + IC.user + '</span><span class="kb-line-text"><b>' + esc(l.name || "Кто-то") + "</b><span>" +
          esc(words[l.action] || l.action) + '</span></span><span></span><span class="kb-line-right">' + esc(when(l.at)) + "</span></div>";
      }).join("") + "</div>";
    }
    if (canEdit(v)) html += '<div class="vlt-actions vlt-left">' + btn("Удалить запись", 'data-v="delete" data-id="' + it.id + '"', "kb-btn--ghost vlt-danger") + "</div>";
    return html + "</div>";
  }

  function editHtml(v) {
    var d = st.draft;
    if (!v || !d) {
      st.view = "list";
      return listHtml(v);
    }
    var t = TYPES[d.data.type] || TYPES.site;
    var html = bar(crumbs(v).concat([[d.id ? "Изменить" : "Новая запись"]]), timer()) + '<div class="kb-page vlt-page vlt-record"><form class="vlt-form" data-v="form" autocomplete="off">';
    html += '<h1 class="screen-title kb-title">' + (d.id ? "Изменить запись" : "Новая запись") + "</h1>";
    if (!d.id) {
      html += '<div class="vlt-types" role="radiogroup" aria-label="Тип записи">' + Object.keys(TYPES).map(function (k) {
        return '<button type="button" class="vlt-type' + (k === d.data.type ? " is-on" : "") + '" data-v="type" data-t="' + k + '" role="radio" aria-checked="' + (k === d.data.type) + '">' +
          IC[k] + "<span>" + esc(TYPES[k].label) + "</span></button>";
      }).join("") + "</div>";
    }
    html += '<label class="vlt-input"><span class="vlt-label">Название</span><input class="pf-input" name="title" value="' + esc(d.data.title || "") + '" placeholder="Сервер прод" maxlength="120" /></label>';
    t.fields.forEach(function (f) {
      var key = f[0], kind = f[3] || "";
      var val = d.data[key] || "";
      if (kind.indexOf("text") === 0) {
        html += '<label class="vlt-input"><span class="vlt-label">' + esc(f[1]) + '</span><textarea class="pf-input" name="' + key + '" rows="' + (kind === "text-secret" ? 8 : 3) + '">' + esc(val) + "</textarea></label>";
      } else if (kind === "secret") {
        html += '<label class="vlt-input"><span class="vlt-label">' + esc(f[1]) + '</span><span class="vlt-pw"><input class="pf-input vlt-mono" name="' + key + '" value="' + esc(val) + '" spellcheck="false" autocomplete="new-password" />' +
          (key === "password" && d.data.type !== "api" ? '<button type="button" class="kb-btn" data-v="gen">' + IC.dice + "<span>Сгенерировать</span></button>" : "") + "</span></label>";
      } else {
        html += '<label class="vlt-input"><span class="vlt-label">' + esc(f[1]) + '</span><input class="pf-input' + (key === "login" || key === "address" ? " vlt-mono" : "") + '" name="' + key + '" type="' + (kind === "date" ? "date" : "text") + '" value="' + esc(val) + '" placeholder="' + esc(f[2] || "") + '" spellcheck="false" /></label>';
      }
    });
    html += '<div class="vlt-actions vlt-left">' + btn("Отмена", 'data-v="cancel"', "kb-btn--ghost") +
      '<button type="submit" class="kb-btn kb-btn--primary"' + busyAttr() + ">Сохранить</button></div>";
    html += '<p class="pf-hint">Запись шифруется в браузере до отправки. Сервер хранит только шифр.</p>';
    return html + "</form></div>";
  }

  function readForm(form) {
    var d = st.draft;
    if (!d) return;
    Array.prototype.forEach.call(form.querySelectorAll("input[name], textarea[name]"), function (el) {
      d.data[el.name] = el.value;
    });
  }

  // ---------- клики ----------

  async function onClick(e) {
    var b = e.target.closest("[data-v]");
    if (!b) return false;
    var a = b.getAttribute("data-v");
    var id = Number(b.getAttribute("data-id"));
    var v = vaultOf(st.vaultId);
    if (a === "saved-check" || a === "form" || a === "code-form") {
      if (a === "saved-check") {
        var fin = b.closest(".vlt-page").querySelector('[data-v="finish"]');
        if (fin) fin.disabled = !b.checked;
      }
      return true;
    }
    if (b.tagName === "INPUT") return true;
    e.preventDefault();
    switch (a) {
      case "home": leave(); break;
      case "back":
        if (st.view === "record" || st.view === "edit") {
          st.view = st.view === "edit" && st.draft && st.draft.id ? "record" : "list";
          st.draft = null;
          paint();
        } else leave();
        break;
      case "list": st.view = "list"; st.itemId = null; st.draft = null; paint(); break;
      case "lock": lock(); break;
      case "setup": startSetup(); break;
      case "print": printCode(); break;
      case "copy-code":
        if (st.setup) navigator.clipboard.writeText(st.setup.code).then(function () { toast("Скопировано — вставьте в надёжное место и очистите буфер"); }, function () {});
        break;
      case "finish": finishSetup(); break;
      case "unlock": unlockPasskey(); break;
      case "device": st.view = "device"; paint(); break;
      case "reset": resetKeys(); break;
      case "add-device": addThisDevice(); break;
      case "skip-device":
        if (st._codeUmk) st._codeUmk.fill(0);
        st._codeUmk = null;
        st.view = "list";
        paint();
        break;
      case "open": st.itemId = id; st.view = "record"; paint(); break;
      case "copy": copyField(id, b.getAttribute("data-f")); break;
      case "reveal": toggleReveal(id); break;
      case "new":
        st.draft = { id: null, data: { type: "server" } };
        st.view = "edit";
        paint();
        break;
      case "edit": {
        var it = v && itemOf(v.id, id);
        if (!it) break;
        st.draft = { id: it.id, data: JSON.parse(JSON.stringify(it.data)) };
        st.view = "edit";
        paint();
        break;
      }
      case "type": {
        var form = b.closest("form");
        if (form) readForm(form);
        st.draft.data.type = b.getAttribute("data-t");
        paint();
        break;
      }
      case "gen": {
        var inp = b.closest(".vlt-pw").querySelector("input");
        inp.value = CR.generatePassword(20);
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        break;
      }
      case "cancel":
        st.view = st.draft && st.draft.id ? "record" : "list";
        st.draft = null;
        paint();
        break;
      case "delete": deleteItem(id); break;
      case "access": openAccess(st.vaultId); break;
      case "vault-more": vaultMenu(b); break;
      case "dismiss-deps": dismissDepartures(st.vaultId); break;
      case "req": requestAccess(st.vaultId); break;
      case "req-cancel": cancelRequest(st.vaultId); break;
      case "req-ok": decideRequest(st.vaultId, b.getAttribute("data-u"), b.getAttribute("data-r")); break;
      case "req-no": decideRequest(st.vaultId, b.getAttribute("data-u"), ""); break;
      case "open-browser":
        if (app().openLink) app().openLink(location.origin + "/webapp/");
        break;
      case "vault": show(id); break;
      case "new-vault": createVault(b.getAttribute("data-org") ? Number(b.getAttribute("data-org")) : null); break;
      case "personal": {
        var pv = st.vaults.filter(function (x) { return x.personal; })[0];
        if (pv) show(pv.id);
        else createVault(null);
        break;
      }
      default: return false;
    }
    return true;
  }

  function vaultMenu(anchor) {
    var vid = st.vaultId;
    var items = [
      { label: "Переименовать", run: function () { renameVault(vid); } },
      { label: "Удалить сейф", danger: true, run: function () { deleteVault(vid); } },
    ];
    if (window.LeoOrgKbDesk && window.LeoOrgKbDesk.openMenu) return window.LeoOrgKbDesk.openMenu(anchor, items);
    renameVault(vid);
  }

  function onSubmit(e) {
    var f = e.target;
    if (f.getAttribute("data-v") === "form") {
      e.preventDefault();
      saveItem();
    } else if (f.getAttribute("data-v") === "code-form") {
      e.preventDefault();
      var code = f.querySelector("input[name=code]").value;
      unlockCode(code);
    }
  }

  function onInput(e) {
    var f = e.target.closest("form[data-v=form]");
    if (f) readForm(f);
  }

  function printCode() {
    var s = st.setup;
    if (!s) return;
    var w = window.open("", "_blank", "width=600,height=500");
    if (!w) return toast("Браузер не дал открыть окно печати");
    w.document.write('<!doctype html><meta charset="utf-8"><title>Аварийный лист сейфов Орбиты</title>' +
      '<body style="font:15px/1.5 -apple-system,system-ui,sans-serif;padding:40px;color:#000;background:#fff">' +
      "<h2>Аварийный лист сейфов Орбиты</h2><p>Код восстановления — откроет ваши сейфы, если не будет ваших устройств.</p>" +
      '<p style="font:600 22px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.06em;border:1px dashed #000;padding:16px;text-align:center">' + esc(s.code) + "</p>" +
      "<p>Дата: " + esc(new Date().toLocaleDateString("ru-RU")) + "</p><p>Храните вне компьютера. Не фотографируйте и не пересылайте. Орбита этот код не знает и восстановить не сможет.</p>" +
      "<script>window.print()<\/script></body>");
    w.document.close();
  }

  // ---------- блоки для дерева и главной ----------

  function orgVaults(orgId) {
    return st.vaults.filter(function (v) { return v.org_id === orgId; });
  }

  function treeHtml(orgId, currentId) {
    if (!st.enabled) return "";
    var list = orgVaults(orgId);
    var html = '<div class="vlt-tree"><div class="kb-org-row vlt-tree-head"><span class="vlt-tree-lock" aria-hidden="true">' + IC.lock + '</span><span class="kb-org-name">Сейфы</span>' +
      '<button type="button" class="kb-n-btn" data-v="new-vault" data-org="' + orgId + '" aria-label="Новый сейф" title="Новый сейф">' + IC.plus + "</button></div>";
    html += list.map(function (v) {
      var cur = currentId === v.id;
      return '<div class="kb-node vlt-node' + (cur ? " is-current" : "") + (v.foreign ? " vlt-foreign" : "") + '" role="treeitem" tabindex="-1" style="--kb-depth:0" data-v="vault" data-id="' + v.id + '"' + (cur ? ' aria-current="page"' : "") + ">" +
        '<span class="kb-chev kb-chev--none" aria-hidden="true"></span><span class="kb-n-ico" aria-hidden="true">' + IC.lock + "</span>" +
        '<span class="kb-n-title">' + esc(v.name) + '</span><span class="kb-n-count">' + (v.foreign ? (v.request && v.request.status === "pending" ? "⏳" : "—") : v.envelope ? v.items : "·") + (v.requests && v.requests.length ? '<i class="vlt-req-dot" title="Просят доступ"></i>' : "") + "</span></div>";
    }).join("");
    return html + "</div>";
  }

  function footHtml(currentId) {
    if (!st.enabled) return "";
    var pv = st.vaults.filter(function (x) { return x.personal; })[0];
    var cur = pv && currentId === pv.id;
    return '<button type="button" class="kb-foot-row' + (cur ? " is-current" : "") + '" data-v="personal">' + IC.lock + "<span>Личный сейф</span>" +
      (pv ? '<span class="kb-foot-val">' + pv.items + "</span>" : "") + "</button>";
  }

  function homeHtml(orgId, orgName, many) {
    if (!st.enabled) return "";
    var list = orgVaults(orgId);
    var html = '<p class="kb-label">Сейфы' + (many ? " · " + esc(orgName) : "") + '<span class="kb-label-note">пароли и ключи — шифруются на ваших устройствах, Лео их не видит</span></p><div class="kb-cards">';
    html += list.map(function (v) {
      return '<button type="button" class="kb-card" data-v="vault" data-id="' + v.id + '"><span class="kb-card-top"><span class="kb-card-ico" aria-hidden="true">' + IC.lock + "</span><b>" + esc(v.name) + "</b></span>" +
        '<span class="kb-card-meta">' + (v.foreign ? "нет доступа · открыт: " + esc(v.access)
          : esc(count(v.items, "запись", "записи", "записей")) + " · открыт: " + esc(v.access) + (v.envelope ? "" : " · ждёт ключ") +
            (v.requests && v.requests.length ? " · " + esc(count(v.requests.length, "запрос", "запроса", "запросов")) + " доступа" : "")) + "</span></button>";
    }).join("");
    html += '<button type="button" class="kb-card kb-card--add" data-v="new-vault" data-org="' + orgId + '"><span class="kb-card-top">' + IC.plus + "<b>Новый сейф</b></span></button></div>";
    return html;
  }

  // Список на телефоне (вкладка без дерева): блок под разделами организации.
  function listBlockHtml() {
    if (!st.enabled || !st.loaded) return "";
    var rows = st.vaults.map(function (v) {
      return '<button type="button" class="pf-row" data-v="vault" data-id="' + v.id + '"><span class="pf-ico kb-ico" aria-hidden="true">' + IC.lock + "</span>" +
        '<span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">' + esc(v.name) + '</span><span class="pf-row-sub">' +
        esc(v.foreign ? "нет доступа · можно запросить" : count(v.items, "запись", "записи", "записей") + " · " + (v.personal ? "только вы" : v.access)) + '</span></span><span class="pf-chev" aria-hidden="true">›</span></span></button>';
    }).join("");
    var orgs = (K() && K().state && K().state.orgs) || [];
    var add = orgs.length ? '<button type="button" class="pf-row pf-add-row" data-v="new-vault" data-org="' + orgs[0].org.id + '"><span class="pf-ico pf-add-ico" aria-hidden="true">' + IC.plus + '</span><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Новый сейф</span></span></span></button>' : "";
    var personal = st.vaults.some(function (v) { return v.personal; }) ? "" :
      '<button type="button" class="pf-row pf-add-row" data-v="personal"><span class="pf-ico pf-add-ico" aria-hidden="true">' + IC.plus + '</span><span class="pf-row-body"><span class="pf-row-text"><span class="pf-row-title">Личный сейф</span></span></span></button>';
    return '<div class="pf-group kb-org-group vlt-list-block"><p class="pf-group-title kb-group-head"><svg class="kb-lock" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Сейфы</p>' +
      '<div class="pf-list">' + rows + add + personal + "</div>" +
      '<p class="pf-hint">Пароли и ключи. Шифруются на вашем устройстве — Орбита и Лео их не видят.</p></div>';
  }

  // ---------- экран на телефоне ----------

  function openMobile() {
    if (!st.mobile) {
      var el = document.createElement("div");
      el.className = "vlt-mobile";
      el.innerHTML = '<div class="vlt-mobile-body"></div>';
      el.addEventListener("click", function (e) { onClick(e); });
      el.addEventListener("submit", onSubmit);
      el.addEventListener("input", onInput);
      document.body.appendChild(el);
      st.mobile = el;
      if (app().syncAppOverlay) app().syncAppOverlay();
    }
    paintMobile();
  }
  function closeMobile() {
    if (st.mobile) st.mobile.remove();
    st.mobile = null;
    if (app().syncAppOverlay) app().syncAppOverlay();
  }
  function paintMobile() {
    if (!st.mobile) return;
    var body = st.mobile.querySelector(".vlt-mobile-body");
    keepInputs(body, function () { body.innerHTML = pageHtml(); });
  }

  // Перерисовка не должна терять набранное (поле кода, форма записи).
  function keepInputs(el, fn) {
    var a = document.activeElement;
    var name = a && el.contains(a) && a.name;
    var codeVal = el.querySelector && el.querySelector(".vlt-code-input");
    codeVal = codeVal ? codeVal.value : "";
    fn();
    var ci = el.querySelector(".vlt-code-input");
    if (ci && codeVal) ci.value = codeVal;
    if (name) {
      var n = el.querySelector('[name="' + name + '"]');
      if (n) n.focus();
    }
  }

  // ---------- для org-kb-desk.js ----------

  function render(el) {
    keepInputs(el, function () { el.innerHTML = pageHtml(); });
  }

  document.addEventListener("submit", function (e) {
    if (e.target.closest && e.target.closest("#kb-desk .vlt-page")) onSubmit(e);
  }, true);
  document.addEventListener("input", function (e) {
    if (e.target.closest && e.target.closest("#kb-desk .vlt-page")) onInput(e);
  }, true);
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.getAttribute && t.getAttribute("data-v") === "saved-check") {
      var fin = t.closest(".vlt-page").querySelector('[data-v="finish"]');
      if (fin) fin.disabled = !t.checked;
    }
  }, true);
  window.addEventListener("pagehide", function () { lock(true); });

  window.LeoVault = {
    load: load,
    enabled: function () { return st.enabled; },
    treeHtml: treeHtml,
    footHtml: footHtml,
    homeHtml: homeHtml,
    listBlockHtml: listBlockHtml,
    render: render,
    onClick: onClick,
    show: show,
    isUnlocked: unlocked,
    lock: lock,
  };
  if (CR && CR.supported) load();
})();
