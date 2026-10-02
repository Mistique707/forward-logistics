// The app shell is cached so the form opens with no signal; API calls always go to the network.
const SHELL = 'field-v1'
const FILES = ['/field/', '/field/manifest.webmanifest', '/favicon.svg']

self.addEventListener('install', (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())))
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone()
    caches.open(SHELL).then((c) => c.put(e.request, copy))
    return r
  }).catch(() => caches.match(e.request).then((m) => m || caches.match('/field/'))))
})
