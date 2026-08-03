'use strict';
const IMG_CACHE   = 'whereabouts-images-v2';
const DATA_CACHE  = 'whereabouts-data-v1';
const SHELL_CACHE = 'whereabouts-shell-v1';
const ALL_CACHES  = [IMG_CACHE, DATA_CACHE, SHELL_CACHE];
// A launch on a dead-but-connected link (no signal, or wifi with no internet)
// must not wait for the browser's long fetch timeout - that is the frozen
// splash screen. If the network hasn't answered by this, we serve the cache.
const NET_TIMEOUT = 3000;

// Added one URL at a time rather than with addAll, which is all-or-nothing in
// two ways that both bit here: it rejects the whole batch if any single request
// fails, and it rejects outright if the list contains duplicates. The list used
// to carry both `self.registration.scope` and './', which resolve to the same
// URL, so the precache rejected every time and the `.catch(() => {})` swallowed
// it. The shell was therefore never precached at install, and a launch that
// went offline before the fetch handler had cached anything showed "Offline and
// not cached yet" instead of the app. Failures are still tolerated per URL, but
// one bad entry can no longer take the rest down with it.
self.addEventListener('install', e => {
  e.waitUntil((async () => {
    // Each asset must be precached into the SAME bucket the fetch handler
    // below reads it from, or it is cached somewhere nothing looks and the
    // offline launch fails anyway. fuse.min.js and counterscale.min.js are
    // served cache-first from IMG_CACHE, not from the shell.
    const c = await caches.open(SHELL_CACHE);
    await Promise.all(['./', './index.html'].map(u => c.add(u).catch(() => {})));
    const assets = await caches.open(IMG_CACHE);
    const libs = ['./fuse.min.js', './counterscale.min.js'];
    await Promise.all(libs.map(u => assets.add(u).catch(() => {})));
    // houses.json is precached too, despite its size (about 3.4 MB), because
    // the app is inert without it: it is the list that search runs against.
    // Leaving it to the fetch handler meant it was cached only once the worker
    // was already controlling the page, which is never true on a first visit,
    // so offline use quietly required a SECOND online visit before it worked.
    // It goes in DATA_CACHE because that is where the fetch handler looks for
    // it; putting it in the shell would cache it somewhere nothing reads.
    const data = await caches.open(DATA_CACHE);
    await data.add('./houses.json').catch(() => {});
  })());
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
  if (url.pathname.includes('/images/') || url.pathname.endsWith('fuse.min.js')
      || url.pathname.endsWith('counterscale.min.js')) {
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
