/* Mini App service worker: shell + offline open for PWA. */
/* Bump CACHE_VERSION при смене precache-списка или критичных ассетов. */
var CACHE_VERSION = "miniapp-v1-20260922-discuss-quote-center";
var SHELL_CACHE = CACHE_VERSION + "-shell";

var PRECACHE_URLS = [
  "./",
  "./index.html",
  "./styles.css?v=20260922-discuss-quote-center",
  "./app.js?v=20260922-discuss-quote-center",
  "./note-html.js?v=20260922-discuss-quote-center",
  "./note-comments.js?v=20260922-discuss-quote-center",
  "./note-rich-editor.js?v=20260922-discuss-quote-center",
  "./icons/arrow-up-right.svg?v=20260922-discuss-quote-center",
  "./icons/chevron-up-muted.svg?v=20260922-discuss-quote-center",
  "./icons/chevron-up-on-fill.svg?v=20260922-discuss-quote-center",
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

function matchShellHtml() {
  return caches.open(SHELL_CACHE).then(function (cache) {
    return cache.match("./index.html").then(function (hit) {
      if (hit) return hit;
      return cache.match("./");
    });
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

  var path = "";
  try {
    path = new URL(url).pathname;
  } catch (_) {}

  if (path.indexOf("/webapp/sw.js") !== -1) return;

  // Navigation: network-first with short timeout, then cached shell (offline open).
  if (isNavigationRequest(request) || isHtmlPath(path)) {
    event.respondWith(
      fetchWithTimeout(request, 2500)
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

  // Versioned assets: cache-first for instant warm start, refresh in background.
  event.respondWith(
    caches.match(request).then(function (cached) {
      var network = fetchWithTimeout(request, 5000)
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
