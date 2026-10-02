// The offline copy's rules (src/dipcast/site/sw.js), run in Node with a stand-in cache and network.
// pytest runs this file (test_site_pages.py); on its own: node --test tests/site_cache.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const SCOPE = 'https://example.org/dipcast/';
const FORECAST = SCOPE + 'data/spots.json';

function worker(fetch, stored = {}) {
  const entries = new Map(Object.entries(stored));
  const handlers = {}, notifications = [], opened = [], added = [];
  const cache = {
    match: async key => entries.get(key)?.clone(),
    put: async (key, res) => { entries.set(key, res); },
    add: async req => { added.push(req); },
  };
  // A worker resolves a relative address against its own; Node's Request needs it absolute.
  class ScopedRequest extends Request { constructor(u, init) { super(typeof u === 'string' ? new URL(u, SCOPE).href : u, init); } }
  const context = vm.createContext({
    fetch, URL, Response, Request: ScopedRequest,
    caches: { open: async name => { opened.push(name); return cache; } },
    self: { registration: { scope: SCOPE, showNotification: async (title, options) => notifications.push({ title, ...options }) }, location: new URL(SCOPE),
            skipWaiting: async () => {}, addEventListener(type, handler) { handlers[type] = handler; } },
    setTimeout() {},   // every case here settles through the network, so the 4 s race never fires
  });
  vm.runInContext(readFileSync(join(__dirname, '../src/dipcast/site/sw.js'), 'utf8'), context);
  return { context, entries, notifications, handlers, opened, added, push: async data => { let pending; handlers.push({ data: { json: () => data }, waitUntil(p) { pending = p; } }); await pending; } };
}

test('a reload asks the server, past the browser cache, and replaces the stored forecast', async () => {
  let asked;
  const { context, entries } = worker(async (_req, init) => { asked = init && init.cache; return new Response('{"generated_at":"new"}'); },
    { [FORECAST]: new Response('{"generated_at":"old"}') });
  const res = await context.networkFirst(new Request(FORECAST));
  assert.equal(asked, 'no-cache');
  assert.equal((await res.json()).generated_at, 'new');
  assert.equal((await entries.get(FORECAST).json()).generated_at, 'new');
});

test('without a connection the stored forecast answers, unchanged', async () => {
  const { context } = worker(async () => { throw new Error('offline'); }, { [FORECAST]: new Response('{"generated_at":"stored"}') });
  assert.equal((await (await context.networkFirst(new Request(FORECAST))).json()).generated_at, 'stored');
});

test('a spot page never opened before is the home page, with a <base> at the scope first', async () => {
  const { context } = worker(async () => { throw new Error('offline'); },
    { [SCOPE]: new Response('<html><head><base href="./"></head><body>App</body></html>') });
  const res = await context.networkFirst({ url: SCOPE + 'spot/ilkley/?source=shared', mode: 'navigate' });
  const html = await res.text();
  assert.equal(res.headers.get('Content-Type'), 'text/html; charset=utf-8');
  assert.ok(html.indexOf(`<base href="${SCOPE}">`) < html.indexOf('<base href="./">'));
});

test('a first visit without a connection fails rather than invent a forecast', async () => {
  const { context } = worker(async () => { throw new Error('offline'); });
  await assert.rejects(context.networkFirst(new Request(FORECAST)), /offline/);
});


test('an expired delivered push displays a check-latest notice, not the old risk', async () => {
  const w = worker(async () => {});
  await w.push({ title: 'Very high today', body: 'Old pollution claim', expires_at: new Date(Date.now() - 1000).toISOString(), url: SCOPE + 'spot/a/' });
  assert.equal(w.notifications[0].title, 'SwimSignal forecast update');
  assert.match(w.notifications[0].body, /expired/);
  assert.equal(w.notifications[0].data.url, SCOPE + 'spot/a/');
  assert.ok(!w.notifications[0].body.includes('Old pollution'));
});

test('a fresh push includes its UK forecast issue time', async () => {
  const w = worker(async () => {});
  await w.push({ title: 'Spot A', body: 'High today', issued_at: '2026-09-30T08:00:00Z', expires_at: new Date(Date.now() + 60000).toISOString() });
  assert.equal(w.notifications[0].title, 'Spot A');
  assert.match(w.notifications[0].body, /High today.*30 Sept.*09:00.*UK time/);
});

test('an invalid explicit expiry cannot show a current pollution claim', async () => {
  const w = worker(async () => {});
  await w.push({ title: 'High today', expires_at: 'not a date' });
  assert.equal(w.notifications[0].title, 'SwimSignal forecast update');
});

test('a legacy notification still works and a null payload cannot crash the handler', async () => {
  const w = worker(async () => {});
  await w.push({ title: 'Legacy', body: 'Message' });
  await w.push(null);
  assert.equal(w.notifications[0].title, 'Legacy');
  assert.equal(w.notifications[1].title, 'SwimSignal');
});

// A fetch event as the browser sends it: what the worker answered, or undefined if it left the
// request to the browser.
function fetchEvent(w, url, mode = 'cors') {
  let answer;
  w.handlers.fetch({ request: { url, method: 'GET', mode }, respondWith(p) { answer = p; } });
  return answer;
}

test('nothing from another site is handled: the map library is this site\'s own', () => {
  const w = worker(async () => new Response('x'));
  assert.equal(fetchEvent(w, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'), undefined);
  assert.equal(fetchEvent(w, 'https://tile.openstreetmap.org/7/63/41.png'), undefined);
  assert.equal(fetchEvent(w, SCOPE + 'data/overflows.geojson'), undefined);
});

test('the map library is the stored copy first, as the fonts are; the site\'s own scripts the network first', async () => {
  const lib = SCOPE + 'vendor/leaflet/leaflet.js', levels = SCOPE + 'levels.js?v=dev';
  const w = worker(async () => new Response('from the network'), { [lib]: new Response('stored'), [levels]: new Response('stored') });
  assert.equal(await (await fetchEvent(w, lib)).text(), 'stored');
  assert.equal(await (await fetchEvent(w, levels)).text(), 'from the network');
});

test('the cache is named by the build and filled from the server, with the scripts at the build\'s stamp', async () => {
  const w = worker(async () => new Response('x'));
  let done;
  w.handlers.install({ waitUntil(p) { done = p; } });
  await done;
  assert.deepEqual(w.opened, ['dipcast-dev']);   // the build writes its stamp over 'dev'
  const urls = w.added.map(r => r.url);
  assert.ok(urls.includes(SCOPE + 'levels.js?v=dev') && urls.includes(SCOPE + 'experience.js?v=dev'), urls.join(' '));
  assert.ok(urls.includes(SCOPE + 'vendor/leaflet/leaflet.js') && urls.includes(SCOPE + 'vendor/leaflet/leaflet.css'), urls.join(' '));
  assert.ok(urls.every(u => u.startsWith(SCOPE)), urls.join(' '));
  assert.ok(w.added.every(r => r.cache === 'no-cache'));
});
