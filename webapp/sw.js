/* Mini App service worker: кэш оболочки для быстрого повторного открытия PWA. */
/* Bump CACHE_VERSION при смене precache-списка или критичных ассетов. */
var CACHE_VERSION = "miniapp-v1-20260907-toc-hits";
var SHELL_CACHE = CACHE_VERSION + "-shell";

var PRECACHE_URLS = [
  "./styles.css?v=20260907-toc-hits",
  "./app.js?v=20260907-toc-hits",
  "./note-html.js?v=20260907-toc-hits",
  "./note-comments.js?v=20260907-toc-hits",
  "./note-rich-editor.js?v=20260907-toc-hits",
  "./icons/arrow-up-right.svg?v=20260907-toc-hits",
  "./icons/chevron-up-muted.svg?v=20260907-toc-hits",
  "./icons/chevron-up-on-fill.svg?v=20260907-toc-hits",
  "./telegram-web-app.js?v=20260831-vpn-pwa",
  "./telegram-widget.js?v=20260831-vpn-pwa",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-180.png",
];

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

function fetchWithTimeout(request, ms) {
  return new Promise(function (resolve, reject) {
    var settled = false;
    var timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      reject(new Error("timeout"));
    }, ms);
    fetch(request).then(
      function (response) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(response);
      },
      function (err) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

function cachePut(cacheName, request, response) {
  if (!response || !response.ok) return Promise.resolve();
  var copy = response.clone();
  return caches.open(cacheName).then(function (cache) {
    return cache.put(request, copy);
  });
}

self.addEventListener("install", function (event) {
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
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys.map(function (key) {
            if (key.indexOf("miniapp-") === 0 && key !== SHELL_CACHE) {
              return caches.delete(key);
            }
            return null;
          })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = request.url;
  if (isApiRequest(url)) return;
  if (!sameOrigin(url)) return;

  // HTML всегда идёт мимо SW: иначе VPN/зависшая сеть держит белый экран.
  if (isNavigationRequest(request)) return;

  var path = "";
  try {
    path = new URL(url).pathname;
  } catch (_) {}

  if (isHtmlPath(path) || path.indexOf("/webapp/sw.js") !== -1) return;

  if (
    path.indexOf("/webapp/") === 0 &&
    (path.endsWith(".css") ||
      path.endsWith(".js") ||
      path.endsWith(".png") ||
      path.endsWith(".webmanifest") ||
      path.endsWith(".svg") ||
      path.endsWith(".ico"))
  ) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        if (cached) {
          fetchWithTimeout(request, 4000)
            .then(function (response) {
              if (response && response.ok) {
                caches.open(SHELL_CACHE).then(function (cache) {
                  cache.put(request, response);
                });
              }
            })
            .catch(function () {});
          return cached;
        }
        return fetchWithTimeout(request, 20000).then(function (response) {
          cachePut(SHELL_CACHE, request, response);
          return response;
        });
      })
    );
  }
});
