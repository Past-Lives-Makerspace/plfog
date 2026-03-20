/**
 * Service Worker for PLFOG PWA
 * Vanilla JS implementation for offline shell caching
 */

const CACHE_NAME = 'plfog-shell-v3';

// Assets to precache on install
const PRECACHE_ASSETS = [
  '/static/offline.html',
  '/static/css/style.css',
  '/static/css/unfold-custom.css'
];

/**
 * Install event - precache shell assets
 */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        return cache.addAll(PRECACHE_ASSETS);
      })
      .then(() => {
        return self.skipWaiting();
      })
  );
});

/**
 * Activate event - clear old caches and claim clients immediately
 */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        );
      })
      .then(() => {
        return self.clients.claim();
      })
  );
});

/**
 * Fetch event - NEVER cache HTML pages (they contain CSRF tokens).
 * Only cache static assets (CSS, JS, images).
 */
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) {
    return;
  }

  // Navigation requests (HTML pages) - always go to network, offline fallback only
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .catch(() => {
          return caches.match('/static/offline.html');
        })
    );
    return;
  }

  // Static assets (CSS, JS, images) - cache-first
  const isStaticAsset =
    request.destination === 'style' ||
    request.destination === 'script' ||
    request.destination === 'image' ||
    url.pathname.startsWith('/static/css/') ||
    url.pathname.startsWith('/static/js/') ||
    url.pathname.startsWith('/static/img/') ||
    url.pathname.startsWith('/static/icons/');

  if (isStaticAsset) {
    event.respondWith(
      caches.match(request)
        .then((cachedResponse) => {
          if (cachedResponse) {
            // Return cached version, update cache in background
            fetch(request)
              .then((response) => {
                if (response.ok) {
                  caches.open(CACHE_NAME).then((cache) => {
                    cache.put(request, response);
                  });
                }
              })
              .catch(() => {});
            return cachedResponse;
          }

          // Not in cache, fetch from network and cache
          return fetch(request)
            .then((response) => {
              if (!response.ok) {
                return response;
              }
              const responseClone = response.clone();
              caches.open(CACHE_NAME).then((cache) => {
                cache.put(request, responseClone);
              });
              return response;
            });
        })
    );
    return;
  }
});
