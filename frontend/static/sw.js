const CACHE = 'solar-audit-shell-v12';
const OFFLINE_APP = '/static/offline.html';
const SHELL = [
  OFFLINE_APP,
  '/static/cleanmax_logo.png',
  '/static/manifest.webmanifest',
  '/static/mobile.css',
  '/static/page-styles.css',
  '/static/mobile-enhance.js'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then(res => res).catch(() => caches.match(OFFLINE_APP))
    );
    return;
  }

  event.respondWith(
    fetch(req).then(res => {
      const url = new URL(req.url);
      if (res.ok && url.origin === location.origin && url.pathname.startsWith('/static/')) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      }
      return res;
    }).catch(() => caches.match(req).then(c => c || (new URL(req.url).pathname === '/static/offline.html' ? caches.match(OFFLINE_APP) : Response.error())))
  );
});

self.addEventListener('sync', event => {
  if (event.tag !== 'solar-audit-sync') return;
  event.waitUntil(
    self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(clients => {
      clients.forEach(client => client.postMessage({type: 'SOLAR_AUDIT_SYNC_REQUEST'}));
    })
  );
});
