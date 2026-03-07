/**
 * Service Worker for PLFOG PWA
 * Vanilla JS implementation for offline shell caching
 */

const CACHE_NAME = 'plfog-shell-v1';

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
        console.log('[SW] Precaching shell assets');
        return cache.addAll(PRECACHE_ASSETS);
      })
      .then(() => {
        console.log('[SW] Install complete, skipping waiting');
        return self.skipWaiting();
      })
  );
});

/**
 * Activate event - claim clients immediately
 */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== CACHE_NAME)
            .map((name) => {
              console.log('[SW] Deleting old cache:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => {
        console.log('[SW] Claiming clients');
        return self.clients.claim();
      })
  );
});

/**
 * Fetch event - network-first for HTML, cache-first for static assets
 */
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) {
    return;
  }

  // Navigation requests (HTML pages) - network-first with offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Clone response for caching
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone);
          });
          return response;
        })
        .catch(() => {
          // Network failed, try cache
          return caches.match(request)
            .then((cachedResponse) => {
              if (cachedResponse) {
                return cachedResponse;
              }
              // Not in cache, show offline page
              return caches.match('/static/offline.html');
            });
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
              .catch(() => {
                // Network failed, that's fine - we served from cache
              });
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
