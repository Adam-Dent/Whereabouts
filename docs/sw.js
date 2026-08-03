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
    // Every asset must be precached into the SAME bucket the fetch handler
    // below reads it from, or it is cached where nothing looks for it and the
    // offline launch fails anyway. The two libraries are app shell, not map
    // images, so they belong here and the fetch handler serves them from here.
    const c = await caches.open(SHELL_CACHE);
    const shell = ['./', './index.html', './fuse.min.js', './counterscale.min.js',
                   './playfair-normal-latin.woff2', './playfair-normal-latin-ext.woff2', './playfair-italic-latin.woff2', './playfair-italic-latin-ext.woff2'];
    await Promise.all(shell.map(u => c.add(u).catch(() => {})));
    // houses.json is precached too, despite its size (about 3.4 MB), because
    // the app is inert without it: it is the list that search runs against.
    // Leaving it to the fetch handler meant it was cached only once the worker
    // was already controlling the page, which is never true on a first visit,
    // so offline use quietly required a SECOND online visit before it worked.
    // It goes in DATA_CACHE because that is where the fetch handler looks for
    // it; putting it in the shell would cache it somewhere nothing reads.
    const data = await caches.open(DATA_CACHE);
    await data.add('./houses.json').catch(() => {});
    // Inside the waitUntil, and last. Called outside it, skipWaiting activates
    // the worker while the precache is still running, and the browser is then
    // free to abandon the rest of the install: the shell entries landed and
    // everything after them did not. Promoting the worker only once the
    // precache is done is what makes "installed" mean "usable offline".
    await self.skipWaiting();
  })());
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
  if (url.pathname.includes('/images/')) {
    e.respondWith(cacheFirst(e.request, IMG_CACHE));
    return;
  }
  // The two libraries are cache-first like the images (they are versioned and
  // never change under a given deploy), but they live in the shell cache with
  // the rest of the app, which is where the install precaches them. They used
  // to be read from IMG_CACHE while being precached into the shell, so on a
  // cold offline launch fuse.min.js was never found, Fuse was undefined and
  // search could not start at all.
  if (url.pathname.endsWith('fuse.min.js') || url.pathname.endsWith('counterscale.min.js')
      || url.pathname.endsWith('.woff2')) {
    e.respondWith(cacheFirst(e.request, SHELL_CACHE));
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
