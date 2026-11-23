// Network first, so a new edition shows as soon as it is published.
// Falls back to the last saved copy when the phone is offline.
const CACHE = "wire-v5";
const CORE = ["./", "index.html", "feed.json", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const key = url.origin + url.pathname; // ignore cache-busting query strings
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(key, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(key).then((hit) => hit || (req.mode === "navigate" ? caches.match(url.origin + url.pathname.replace(/[^/]*$/, "")) : undefined))
      )
  );
});
