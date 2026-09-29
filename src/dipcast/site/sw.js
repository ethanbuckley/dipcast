// The page and the latest forecast kept on the device, so the site opens without signal at the
// water's edge; the page then says how old the forecast is (index.html, freshness()).
//
// Pages and data: the network first, the stored copy if the network fails or takes over 4 s
// (a slow answer still refreshes the stored copy when it arrives). Icons and the map library:
// the stored copy first, refreshed in the background. Map tiles are OpenStreetMap's and the
// overflow layer is 6 MB, so neither is stored here.
//
// To retire this worker, publish a sw.js that unregisters itself: a deleted file leaves the
// installed worker running on visitors' devices.
const CACHE = 'dipcast-v1';
const TIMEOUT_MS = 4000;
const SHELL = ['./', 'data/spots.json', 'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'];

self.addEventListener('install', e => {
  // One missing file must not stop the rest being stored.
  e.waitUntil(caches.open(CACHE)
    .then(c => Promise.all(SHELL.map(u => c.add(new Request(u, { mode: 'cors', credentials: 'omit' })).catch(() => null))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('dipcast-') && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url), here = url.origin === self.location.origin;
  if (!here && url.hostname !== 'unpkg.com') return;           // tiles, the page-view counter: not ours to keep
  if (here && url.pathname.endsWith('/data/overflows.geojson')) return;
  const fresh = req.mode === 'navigate' || (here && /\.(html|json)$/.test(url.pathname));
  e.respondWith(fresh ? networkFirst(req) : storedFirst(req));
});

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  const net = fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; });
  net.catch(() => {});   // when the stored copy answers, a later network failure is expected
  try {
    return await Promise.race([net, new Promise((_, no) => setTimeout(() => no(new Error('slow')), TIMEOUT_MS))]);
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: req.mode === 'navigate' });
    if (hit) return hit;
    // A spot's page never opened before: the home page stands in, and reads the spot from the
    // address. Spot pages sit two levels down, so it gets their <base> to keep its links working.
    const home = req.mode === 'navigate' ? await cache.match('./') : undefined;
    if (!home) return net;
    if (!/\/spot\/[^/]+\/$/.test(new URL(req.url).pathname)) return home;
    const html = (await home.text()).replace('<head>', '<head>\n<base href="../../">');
    return new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
}

async function storedFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req.url);
  const net = fetch(req.url, { mode: 'cors', credentials: 'omit' })
    .then(res => { if (res.ok) cache.put(req.url, res.clone()); return res; })
    .catch(() => null);
  return hit || (await net) || Response.error();
}
