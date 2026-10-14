// Service Worker：缓存静态资源，支持离线查看外壳
const CACHE = 'birding-v2';
const STATIC = [
  '/', '/manifest.json',
  '/static/css/app.css',
  '/static/js/ui.js', '/static/js/api.js', '/static/js/wall.js',
  '/static/js/map.js', '/static/js/viz.js', '/static/js/rec.js',
  '/static/js/albums.js', '/static/js/review.js', '/static/js/run.js',
  '/static/js/app.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
});

self.addEventListener('fetch', e => {
  // API 与图片不缓存（数据会变化）
  if (e.request.url.includes('/api/') || e.request.url.includes('/img')) return;
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});
