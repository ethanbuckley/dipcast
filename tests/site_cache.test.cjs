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
  const cache = {
    match: async key => entries.get(key)?.clone(),
    put: async (key, res) => { entries.set(key, res); },
  };
  const context = vm.createContext({
    fetch, URL, Response, Request,
    caches: { open: async () => cache },
    self: { registration: { scope: SCOPE }, location: new URL(SCOPE), addEventListener() {} },
    setTimeout() {},   // every case here settles through the network, so the 4 s race never fires
  });
  vm.runInContext(readFileSync(join(__dirname, '../src/dipcast/site/sw.js'), 'utf8'), context);
  return { context, entries };
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
