// Let AI Explain service worker: makes the app installable and keeps a copy of the app shell
// (the page, manifest and icons). Only those files are cached. API calls, sign-in, and anything
// from other sites always go straight to the network and are never stored.
const CACHE = 'let-ai-explain-shell-v2';
const SHELL = ['/', '/providers.js', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('let-ai-explain-') && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Shell files: network first (so updates show up right away), the cached copy only if the network fails.
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  const path = url.pathname === '/index.html' ? '/' : url.pathname;
  if (!SHELL.includes(path)) return;
  e.respondWith(fetch(req).then(res => {
    if (res.ok && res.type === 'basic') { const copy = res.clone(); caches.open(CACHE).then(c => c.put(path, copy)); }
    return res;
  }).catch(() => caches.match(path)));
});
