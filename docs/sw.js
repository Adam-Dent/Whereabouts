'use strict';
const IMG_CACHE   = 'whereabouts-images-v2';
const DATA_CACHE  = 'whereabouts-data-v1';
const SHELL_CACHE = 'whereabouts-shell-v1';
const ALL_CACHES  = [IMG_CACHE, DATA_CACHE, SHELL_CACHE];
// A launch on a dead-but-connected link (no signal, or wifi with no internet)
// must not wait for the browser's long fetch timeout - that is the frozen
// splash screen. If the network hasn't answered by this, we serve the cache.
const NET_TIMEOUT = 3000;

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then(c => c.addAll([
      self.registration.scope,
      './',
      './index.html',
      './fuse.min.js',
    ]).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => !ALL_CACHES.includes(k)).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.pathname.includes('/images/') || url.pathname.endsWith('fuse.min.js')) {
    e.respondWith(cacheFirst(e.request, IMG_CACHE));
    return;
  }
  if (url.pathname.endsWith('houses.json')) {
    e.respondWith(networkFirst(e.request, DATA_CACHE));
    return;
  }
  e.respondWith(networkFirst(e.request, SHELL_CACHE));
});

async function cacheFirst(req, name) {
  const c = await caches.open(name);
  const cached = await c.match(req);
  if (cached) return cached;
  try {
    const resp = await fetch(req);
    if (resp.ok) c.put(req, resp.clone());
    return resp;
  } catch (_) { if (cached) return cached; throw _; }
}

// Network-first, but bounded: race the fetch against NET_TIMEOUT. If the network
// is fast we get the freshest copy (and cache it); if it stalls or fails we fall
// back to the cached copy at once instead of hanging. The fetch keeps running in
// the background so a slow network still refreshes the cache for next time.
async function networkFirst(req, name) {
  const c = await caches.open(name);
  const net = fetch(req)
    .then(resp => { if (resp && resp.ok) c.put(req, resp.clone()); return resp; })
    .catch(() => null);
  const raced = await Promise.race([
    net,
    new Promise(res => setTimeout(() => res('__timeout__'), NET_TIMEOUT)),
  ]);
  if (raced && raced !== '__timeout__') return raced;
  const cached = await c.match(req);
  if (cached) return cached;
  const late = await net;
  return late || new Response('Offline and not cached yet.',
    { status: 503, headers: { 'Content-Type': 'text/plain' } });
}
