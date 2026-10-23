/* Mini App service worker: shell + offline open for PWA. */
/* Bump CACHE_VERSION при смене precache-списка или критичных ассетов. */
var CACHE_VERSION = "miniapp-v1-20261023-notes4";
var SHELL_CACHE = CACHE_VERSION + "-shell";
var CANONICAL_ORIGIN = "https://assistent.networ.ru";
var OBSOLETE_HOSTS = {
  "assistant.obuchat.me": 1,
  "www.assistant.obuchat.me": 1,
};

var PRECACHE_URLS = [
  "./",
  "./index.html",
  "./styles.css?v=20261023-notes4",
  "./app.js?v=20261023-notes4",
  "./mail.js?v=20261023-mail6",
  "./mail-inbox.js?v=20261023-reply1",
  "./digest-chats.js?v=20261023-notes4",
  "./digest-market.js?v=20261023-notes4",
  "./web-canvas.js?v=20261022-focuschip",
  "./launch.html",
  "./manifest.webmanifest",
  "./note-drafts.js?v=20261005-heading-pdf",
  "./icons/arrow-up-right.svg?v=20261003-month-instances",
  "./icons/chevron-up-muted.svg?v=20261003-month-instances",
  "./icons/chevron-up-on-fill.svg?v=20261003-month-instances",
  "./telegram-web-app.js?v=20260831-vpn-pwa",
  "./telegram-widget.js?v=20260831-vpn-pwa",
  "./manifest.webmanifest",
  "./icons/icon-192.png?v=20261022-brand",
  "./icons/icon-512.png?v=20261022-brand",
  "./icons/icon-180.png?v=20261022-brand",
];

function isObsoleteHost() {
  try {
    return !!OBSOLETE_HOSTS[(self.location.hostname || "").toLowerCase()];
  } catch (_) {
    return false;
  }
}

function canonicalUrlFor(requestUrl) {
  try {
    var u = new URL(requestUrl, self.location.href);
    return CANONICAL_ORIGIN + u.pathname + u.search + u.hash;
  } catch (_) {
    return CANONICAL_ORIGIN + "/webapp/";
  }
}

function sameOrigin(url) {
  try {
    return new URL(url, self.location.href).origin === self.location.origin;
  } catch (_) {
    return false;
  }
}

function isApiRequest(url) {
  try {
    var u = new URL(url, self.location.href);
    return u.pathname.indexOf("/api/") === 0;
  } catch (_) {
    return false;
  }
}

function isNavigationRequest(request) {
  return request.mode === "navigate" || request.destination === "document";
}

function isHtmlPath(path) {
  return !path || path === "/webapp" || path === "/webapp/" || path.indexOf(".html") !== -1;
}

function isShellAsset(path) {
  return (
    path.indexOf("/webapp/") === 0 &&
    (path.endsWith(".css") ||
      path.endsWith(".js") ||
      path.endsWith(".png") ||
      path.endsWith(".webmanifest") ||
      path.endsWith(".svg") ||
      path.endsWith(".ico") ||
      path.endsWith(".woff") ||
      path.endsWith(".woff2"))
  );
}

function cachePut(cacheName, request, response) {
  if (!response || !response.ok) return Promise.resolve();
  var copy = response.clone();
  return caches.open(cacheName).then(function (cache) {
    return cache.put(request, copy);
  });
}

function matchShellHtml() {
  return caches.open(SHELL_CACHE).then(function (cache) {
    return cache.match("./index.html").then(function (hit) {
      if (hit) return hit;
      return cache.match("./");
    });
  });
}

function clearAllMiniappCaches() {
  return caches.keys().then(function (keys) {
    return Promise.all(
      keys.map(function (key) {
        if (String(key).indexOf("miniapp-") === 0) return caches.delete(key);
        return null;
      })
    );
  });
}

function migrateClients() {
  return self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
    clients.forEach(function (client) {
      try {
        var dest = canonicalUrlFor(client.url || CANONICAL_ORIGIN + "/webapp/");
        if (typeof client.navigate === "function") {
          client.navigate(dest);
        } else if (typeof client.postMessage === "function") {
          client.postMessage({ type: "leo-migrate-origin", url: dest });
        }
      } catch (_) {}
    });
  });
}

self.addEventListener("install", function (event) {
  if (isObsoleteHost()) {
    event.waitUntil(
      clearAllMiniappCaches()
        .then(function () {
          return self.skipWaiting();
        })
        .catch(function () {
          return self.skipWaiting();
        })
    );
    return;
  }
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then(function (cache) {
        return cache.addAll(PRECACHE_URLS);
      })
      .then(function () {
        return self.skipWaiting();
      })
      .catch(function (err) {
        console.warn("[sw] precache failed", err);
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    clearAllMiniappCaches()
      .then(function () {
        return self.clients.claim();
      })
      .then(function () {
        if (isObsoleteHost()) return migrateClients();
        return self.clients.matchAll({ type: "window" }).then(function (clients) {
          clients.forEach(function (client) {
            try {
              if (client && client.url && typeof client.navigate === "function") {
                client.navigate(client.url);
              }
            } catch (_) {}
          });
        });
      })
      .then(function () {
        if (!isObsoleteHost()) return null;
        return self.registration.unregister().catch(function () {});
      })
  );
});

self.addEventListener("push", function (event) {
  var payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    try {
      payload = { body: event.data ? event.data.text() : "" };
    } catch (_) {}
  }
  var title = String((payload && payload.title) || "Orbita");
  var body = String((payload && payload.body) || "Новое сообщение");
  var url = String((payload && payload.url) || "./");
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      data: { url: url },
      icon: "./icons/icon-192.png",
      badge: "./icons/icon-192.png",
    })
  );
});

// Не начинается с "miniapp-", поэтому clearAllMiniappCaches его не трогает.
var DEEPLINK_CACHE = "leo-deeplink";

function pendingDeepLinkKey() {
  return new URL("./__pending-deeplink", self.location.href).href;
}

function savePendingDeepLink(url) {
  if (!self.caches) return Promise.resolve();
  return caches
    .open(DEEPLINK_CACHE)
    .then(function (cache) {
      var body = JSON.stringify({ url: url, ts: Date.now() });
      return cache.put(
        pendingDeepLinkKey(),
        new Response(body, { headers: { "Content-Type": "application/json" } })
      );
    })
    .catch(function () {});
}

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var raw = (event.notification.data && event.notification.data.url) || "./";
  var url = raw;
  try {
    url = new URL(raw, self.location.href).href;
  } catch (_) {}
  // iOS PWA: нет WindowClient.navigate, а openWindow может открыть start_url —
  // поэтому ссылку дублируем в Cache Storage, приложение заберёт её при старте/возврате.
  event.waitUntil(
    savePendingDeepLink(url).then(function () {
      return self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
        for (var i = 0; i < clientList.length; i++) {
          var client = clientList[i];
          var href = "";
          try {
            href = client.url || "";
          } catch (_) {}
          if (!href || href.indexOf("/webapp") === -1) continue;
          try {
            client.postMessage({ type: "leo-open-url", url: url });
          } catch (_) {}
          if (client.focus) return client.focus().catch(function () {});
          return;
        }
        if (self.clients.openWindow) return self.clients.openWindow(url);
      });
    })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = request.url;
  if (isApiRequest(url)) return;

  var path = "";
  try {
    path = new URL(url).pathname;
  } catch (_) {}

  // Старый PWA на assistant.obuchat.me: не отдаём кэш, уводим на канонический домен.
  if (isObsoleteHost()) {
    if (isNavigationRequest(request) || isHtmlPath(path) || path.indexOf("/webapp") === 0) {
      event.respondWith(Response.redirect(canonicalUrlFor(url), 302));
    }
    return;
  }

  if (!sameOrigin(url)) return;

  if (path.indexOf("/webapp/sw.js") !== -1) return;

  // Browser/VPN: never abort HTML with a short timeout (that served a stale shell).
  if (isNavigationRequest(request) || isHtmlPath(path)) {
    event.respondWith(
      fetch(request)
        .then(function (response) {
          if (response && response.ok) {
            cachePut(SHELL_CACHE, "./index.html", response.clone());
            cachePut(SHELL_CACHE, "./", response.clone());
            return response;
          }
          return matchShellHtml().then(function (cached) {
            return cached || response;
          });
        })
        .catch(function () {
          return matchShellHtml().then(function (cached) {
            return cached || Response.error();
          });
        })
    );
    return;
  }

  if (!isShellAsset(path)) return;

  // Ассеты с ?v= — network-first (иначе PWA долго отдаёт старый app/editor JS).
  var hasVersion = false;
  try {
    hasVersion = !!(new URL(url).searchParams.get("v"));
  } catch (_) {}

  if (hasVersion) {
    event.respondWith(
      fetch(request)
        .then(function (response) {
          if (response && response.ok) cachePut(SHELL_CACHE, request, response);
          return response;
        })
        .catch(function () {
          return caches.match(request).then(function (cached) {
            return cached || Response.error();
          });
        })
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(function (cached) {
      var network = fetch(request)
        .then(function (response) {
          if (response && response.ok) cachePut(SHELL_CACHE, request, response);
          return response;
        })
        .catch(function () {
          return null;
        });
      if (cached) {
        network.catch(function () {});
        return cached;
      }
      return network.then(function (response) {
        if (response) return response;
        return Response.error();
      });
    })
  );
});
