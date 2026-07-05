// Minimal service worker — makes the app installable + gives an offline app shell.
// It NEVER caches API/media/audio (those must always hit the live server).
const CACHE = "duff-shell-v1";
const SHELL = ["/manifest.json", "/favicon.svg", "/icon-192.png", "/logo.svg"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (["/api/", "/media/", "/version/", "/download/", "/cover/"].some((p) => u.pathname.startsWith(p))) return; // live only
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
