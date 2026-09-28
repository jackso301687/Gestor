const CACHE = 'pms-apontamento-v6';
const ASSETS = ['./index.html','styles.css','app.js','manifest.webmanifest','icons/icon.svg','../tokens.css','../local-launch.js'];
const assetURLs = new Set(ASSETS.map(path => new URL(path, self.location).href));

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k.startsWith('pms-apontamento-') && k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  const fieldPage = event.request.mode === 'navigate' && (url.pathname.endsWith('/campo/') || url.pathname.endsWith('/campo/index.html'));
  if (!fieldPage && !assetURLs.has(url.href)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const key = fieldPage ? new URL('./index.html', self.location).href : event.request;
    try {
      const response = await fetch(event.request);
      if (response.ok) await cache.put(key, response.clone());
      return response;
    } catch (e) {
      const cached = await cache.match(key);
      if (cached) return cached;
      throw e;
    }
  })());
});
