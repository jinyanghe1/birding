// Service Worker：静态资源网络优先，离线时才回退缓存。
// 版本必须随缓存策略/资源清单变化更新，避免旧浏览器一直运行过期 JS。
const CACHE = 'birding-v4-20261116';
const STATIC = [
  '/', '/manifest.json',
  '/static/css/app.css',
  '/static/js/ui.js', '/static/js/api.js', '/static/js/wall.js',
  '/static/js/map.js', '/static/js/viz.js', '/static/js/rec.js',
  '/static/js/species_picker.js', '/static/js/review.js', '/static/js/run.js',
  '/static/js/app.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  // API、图片和第三方资源不由 SW 拦截。
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/img') || url.pathname.startsWith('/thumbs/') ||
      url.pathname.startsWith('/full/')) return;

  // 在线时永远先取最新版本；网络失败才用缓存，兼顾更新与离线外壳。
  e.respondWith(
    fetch(e.request).then(response => {
      if (response.ok) {
        const copy = response.clone();
        e.waitUntil(caches.open(CACHE).then(c => c.put(e.request, copy)));
      }
      return response;
    }).catch(() => caches.match(e.request).then(cached => {
      if (cached) return cached;
      if (e.request.mode === 'navigate') return caches.match('/');
      return Response.error();
    }))
  );
});
