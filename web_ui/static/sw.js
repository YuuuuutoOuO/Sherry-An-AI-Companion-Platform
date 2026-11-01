/**
 * Sherry Console — Progressive Web App Service Worker (sw.js)
 * =============================================================
 * Provides Cache-First / Stale-While-Revalidate offline caching for
 * static assets, app shell, and dynamic mod views.
 * Ensures 0ms instant launch even on high-latency VPN connections.
 */

const CACHE_NAME = 'sherry-console-v15';
const APP_SHELL = [
  '/',
  '/static/style.css?v=22',
  '/static/theme.css?v=1',
  '/static/app.js?v=28',
  '/static/chart.min.js?v=1',
  '/static/sherry_avatar_cutout.png',
  '/static/sherry_live2d.css?v=17',
  '/static/sherry_live2d.js?v=19',
];

// 1. Install: Pre-cache core app shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pre-caching App Shell...');
      return cache.addAll(APP_SHELL).catch((err) => {
        console.warn('[SW] App Shell pre-cache partial failure:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// 2. Activate: Clean up previous cache versions and claim clients
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('[SW] Removing legacy cache:', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// 3. Fetch: Cache-First / Stale-While-Revalidate for static & mod views; Network-Only for dynamic data APIs
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only intercept GET requests from the same origin
  if (req.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // Dynamic Data APIs: Network-first, do not cache DB/live data
  const isDataApi = url.pathname.startsWith('/api/') && !url.pathname.endsWith('/view');
  if (isDataApi || url.pathname === '/sw.js' || url.pathname === '/api/version') {
    return;
  }

  // Static assets and App Shell (/ or /static/... or /api/mod/.../view): Stale-While-Revalidate
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cachedResponse = await cache.match(req);

      // Fetch from network in background to update cache
      const fetchPromise = fetch(req).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          cache.put(req, networkResponse.clone());
        }
        return networkResponse;
      }).catch((err) => {
        // Network failure (offline/VPN timeout)
        return cachedResponse;
      });

      // Return cached version immediately if available, otherwise wait for network
      return cachedResponse || fetchPromise;
    })
  );
});

// 4. Message: Support immediate reload on new version
self.addEventListener('message', (event) => {
  if (event.data && event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});
