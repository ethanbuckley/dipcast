// The page and the latest forecast kept on the device, so the site opens without signal at the
// water's edge; the page then says how old the forecast is (index.html, freshness()).
//
// Pages, data and the site's own scripts (levels.js must match the page it came with): the network
// first, the stored copy if the network fails or takes over 4 s (a slow answer still refreshes the
// stored copy when it arrives). Icons and the map library: the stored copy first, refreshed in
// the background. Map tiles are OpenStreetMap's and the overflow layer is 6 MB, so neither is
// stored here.
//
// To retire this worker, publish a sw.js that unregisters itself: a deleted file leaves the
// installed worker running on visitors' devices.
const CACHE = 'dipcast-v1';
const TIMEOUT_MS = 4000;
const SHELL = ['./', 'levels.js', 'data/spots.json', 'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png',
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
  const fresh = req.mode === 'navigate' || (here && /\.(html|json|js)$/.test(url.pathname));
  e.respondWith(fresh ? networkFirst(req) : storedFirst(req));
});

// A page is stored under its address without the query, so ?spot= or ?fbclid= links cannot leave an
// older copy that an offline or slow visit would find first.
const key = req => { const u = new URL(req.url); if (req.mode === 'navigate') u.search = ''; u.hash = ''; return u.href; };

async function networkFirst(req) {
  const cache = await caches.open(CACHE), k = key(req);
  const net = fetch(req).then(res => { if (res.ok) cache.put(k, res.clone()); return res; });
  net.catch(() => {});   // when the stored copy answers, a later network failure is expected
  try {
    return await Promise.race([net, new Promise((_, no) => setTimeout(() => no(new Error('slow')), TIMEOUT_MS))]);
  } catch (err) {
    const hit = await cache.match(k);
    if (hit) return hit;
    // A page never opened before (a spot's, say): the home page stands in and reads the spot from
    // the address. Its first <base>, at this worker's scope, keeps its links working at any depth.
    const home = req.mode === 'navigate' ? await cache.match(self.registration.scope) : undefined;
    if (!home) return net;
    const html = (await home.text()).replace('<head>', `<head>\n<base href="${self.registration.scope}">`);
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

// Alerts (push/ sends them; index.html, "alerts"): show what arrived, and open its page when
// tapped. Only this site's own pages open, whatever a message says.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Dipspot', {
    body: d.body || '', tag: d.tag, data: { url: d.url }, icon: 'icons/icon-192.png', badge: 'icons/icon-192.png' }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const scope = self.registration.scope, u = (e.notification.data || {}).url;
  let url = scope;
  try { const v = new URL(u, scope).href; if (v.startsWith(scope)) url = v; } catch (err) { /* the home page */ }
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(wins => {
    const w = wins.find(c => c.url.startsWith(scope));
    return w ? w.focus().then(c => c.navigate(url)).catch(() => self.clients.openWindow(url)) : self.clients.openWindow(url);
  }));
});
