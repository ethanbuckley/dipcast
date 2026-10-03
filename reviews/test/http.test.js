import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { forgetOldHits, sha256 } from '../src/index.js';
import { LIMITS } from '../src/rules.js';
import { FakeD1, FakeKV, hasBytes, jpeg } from './helpers.js';

const ORIGIN = 'https://swimsignal.co.uk';
const BASE = 'https://swimsignal-reviews.example.workers.dev';
const ADMIN = 'an-admin-token-long-enough-to-guess-never';
const makeEnv = (extra = {}) => ({ DB: new FakeD1(), PHOTOS: new FakeKV(), ALLOWED_ORIGIN: ORIGIN, SITE_URL: 'https://swimsignal.co.uk/', ADMIN_TOKEN: ADMIN, ...extra });
const today = new Date().toISOString().slice(0, 10);

// A review as the page sends it: a multipart form, with its length, as a browser sends one.
async function form(fields, photos = []) {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.append(k, v);
  photos.forEach((p, n) => {
    f.append(`photo${n}`, new Blob([p.full], { type: 'image/jpeg' }), `photo${n}.jpg`);
    f.append(`thumb${n}`, new Blob([p.thumb], { type: 'image/jpeg' }), `thumb${n}.jpg`);
  });
  const r = new Response(f);
  return { body: await r.arrayBuffer(), type: r.headers.get('Content-Type') };
}

const REVIEW = { spot: 'wharfe-burnsall', again: 'yes', swam_on: today, text: 'Clear and cold.\r\n\r\n\r\nGood steps in.', name: '  Sam  ' };
const PHOTO = { full: jpeg({ exif: true }), thumb: jpeg({ width: 320, height: 240, exif: true }) };

async function send(env, fields = REVIEW, photos = [], { origin = ORIGIN, ip = '203.0.113.7', length } = {}) {
  const { body, type } = await form(fields, photos);
  return worker.fetch(new Request(BASE + '/reviews', {
    method: 'POST', body,
    headers: { 'Content-Type': type, 'Content-Length': String(length ?? body.byteLength), 'CF-Connecting-IP': ip, ...(origin ? { Origin: origin } : {}) },
  }), env);
}

const post = (env, path, data, { origin = ORIGIN, auth, ip = '203.0.113.7' } = {}) => worker.fetch(new Request(BASE + path, {
  method: 'POST', body: JSON.stringify(data),
  headers: { 'Content-Type': 'application/json', 'CF-Connecting-IP': ip, ...(origin ? { Origin: origin } : {}), ...(auth ? { Authorization: 'Bearer ' + auth } : {}) },
}), env);
const get = (env, path, auth) => worker.fetch(new Request(BASE + path, { headers: auth ? { Authorization: 'Bearer ' + auth } : {} }), env);
const publish = (env, id) => post(env, '/admin/decide', { id, action: 'publish' }, { origin: null, auth: ADMIN });

test('a review from the site is stored waiting, its text cleaned and its photos without metadata', async () => {
  const env = makeEnv();
  const res = await send(env, { ...REVIEW, consent: 'yes' }, [PHOTO, PHOTO]);
  assert.equal(res.status, 201);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  const { id, token } = await res.json();
  assert.match(id, /^[0-9a-f]{20}$/);
  assert.match(token, /^[A-Za-z0-9_-]{43}$/);
  const [row] = env.DB.rows('SELECT * FROM reviews');
  assert.equal(row.status, 'pending');
  assert.equal(row.spot, 'wharfe-burnsall');
  assert.equal(row.again, 1);
  assert.equal(row.body, 'Clear and cold.\n\nGood steps in.');
  assert.equal(row.name, 'Sam');
  assert.equal(row.token_hash, await sha256(token), 'only a hash of the key is kept');
  assert.deepEqual(JSON.parse(row.photos), [{ w: 1280, h: 960, tw: 320, th: 240 }, { w: 1280, h: 960, tw: 320, th: 240 }]);
  assert.deepEqual([...env.PHOTOS.map.keys()].sort(), [`p:${id}:0`, `p:${id}:1`, `t:${id}:0`, `t:${id}:1`]);
  for (const bytes of env.PHOTOS.map.values()) assert.ok(!hasBytes(bytes, 'Exif') && !hasBytes(bytes, 'GPSLatitude'));
  // Nothing about the sender: no address, and the rate-limit key is a hash.
  const [hit] = env.DB.rows('SELECT * FROM hits');
  assert.ok(!JSON.stringify(row).includes('203.0.113.7') && !hit.key.includes('203.0.113'));
  assert.match(hit.key, /^[0-9a-f]{32}$/);
});

test('only the site may send, and a preflight is answered for it', async () => {
  const env = makeEnv();
  assert.equal((await send(env, REVIEW, [], { origin: 'https://elsewhere.example' })).status, 403);
  assert.equal((await send(env, REVIEW, [], { origin: null })).status, 403);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 0);
  const pre = await worker.fetch(new Request(BASE + '/reviews/report', { method: 'OPTIONS', headers: { Origin: ORIGIN, 'Access-Control-Request-Method': 'POST' } }), env);
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.equal(pre.headers.get('Access-Control-Allow-Methods'), 'POST');
  assert.equal((await worker.fetch(new Request(BASE + '/reviews', { headers: { Origin: ORIGIN } }), env)).status, 405);
});

test('a review that is not one is refused, and nothing is kept', async () => {
  const env = makeEnv();
  const cases = [
    [{ ...REVIEW, spot: '../etc' }, [], /bad spot id/],
    [{ ...REVIEW, again: 'maybe' }, [], /would swim here again/],
    [{ ...REVIEW, swam_on: '2999-01-01' }, [], /in the future/],
    [{ ...REVIEW, swam_on: '2026-02-30' }, [], /not a real date/],
    [{ ...REVIEW, text: 'x'.repeat(1501) }, [], /at most 1500/],
    [{ ...REVIEW, name: 'n'.repeat(41) }, [], /at most 40/],
    [{ ...REVIEW, text: 'Lovely. Book at www.example.com' }, [], /web addresses/],
    [{ ...REVIEW, name: 'riverswims.co.uk' }, [], /web addresses/],
    [REVIEW, [PHOTO], /photos are yours/],   // no consent
    [{ ...REVIEW, consent: 'yes' }, [{ full: new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]), thumb: PHOTO.thumb }], /photo 1: not a JPEG/],
    [{ ...REVIEW, consent: 'yes' }, [{ full: jpeg({ width: 4000, height: 3000 }), thumb: PHOTO.thumb }], /photo 1 is too large/],
    [{ ...REVIEW, consent: 'yes' }, [PHOTO, PHOTO, PHOTO, PHOTO], /at most 3 photos/],
  ];
  for (const [i, [fields, photos, message]] of cases.entries()) {
    const res = await send(env, fields, photos, { ip: `192.0.2.${i}` });   // one connection each: the limit is tested below
    assert.equal(res.status, 400, String(message));
    assert.match(await res.text(), message);
  }
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 0);
  assert.equal(env.PHOTOS.map.size, 0);
  assert.equal((await send(env, REVIEW, [], { length: 7 * 1024 * 1024 })).status, 413);
});

test('a script that fills in the hidden field is told it worked, and nothing is kept', async () => {
  const env = makeEnv();
  const res = await send(env, { ...REVIEW, website: 'https://spam.example' });
  assert.equal(res.status, 201);
  assert.match((await res.json()).id, /^[0-9a-f]{20}$/);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 0);
});

test(`one connection can send ${LIMITS.review} reviews a day; another is not held up`, async () => {
  const env = makeEnv();
  for (let i = 0; i < LIMITS.review; i++) assert.equal((await send(env)).status, 201);
  const over = await send(env);
  assert.equal(over.status, 429);
  assert.equal(over.headers.get('Access-Control-Allow-Origin'), ORIGIN, 'the page can read why');
  assert.equal((await send(env, REVIEW, [], { ip: '198.51.100.2' })).status, 201);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, LIMITS.review + 1);
  // The daily cron forgets the counts from before yesterday.
  env.DB.db.prepare("INSERT INTO hits (key, day, n) VALUES ('old', '2000-01-01', 3)").run();
  await forgetOldHits(env);
  assert.deepEqual(env.DB.rows("SELECT key FROM hits WHERE key = 'old'"), []);
  assert.equal(env.DB.rows('SELECT * FROM hits').length, 2);
});

test('the queue needs the admin token, and publishing makes a review public', async () => {
  const env = makeEnv();
  const { id } = await (await send(env, { ...REVIEW, consent: 'yes' }, [PHOTO])).json();
  assert.equal((await get(env, '/admin/queue')).status, 401);
  assert.equal((await get(env, '/admin/queue', 'wrong')).status, 401);
  assert.equal((await get(makeEnv({ ADMIN_TOKEN: undefined }), '/admin/queue', 'anything')).status, 503);
  const q = await (await get(env, '/admin/queue', ADMIN)).json();
  assert.deepEqual(q.pending.map((r) => r.id), [id]);
  assert.equal(q.pending[0].text, 'Clear and cold.\n\nGood steps in.');
  assert.ok(!('token_hash' in q.pending[0]));
  // Waiting: neither the review nor its photo is public, though the admin sees the photo.
  assert.deepEqual((await (await get(env, '/published')).json()).reviews, []);
  assert.equal((await get(env, `/photos/${id}-0.jpg`)).status, 404);
  const private_ = await get(env, `/photos/${id}-0-t.jpg`, ADMIN);
  assert.equal(private_.status, 200);
  assert.equal(private_.headers.get('Cache-Control'), 'private, no-store');
  assert.equal(new Uint8Array(await private_.arrayBuffer())[0], 0xff);
  assert.equal((await post(env, '/admin/decide', { id, action: 'publish' }, { origin: null, auth: 'wrong' })).status, 401);
  assert.equal((await publish(env, id)).status, 204);
  const pub = await (await get(env, '/published')).json();
  assert.equal(pub.reviews.length, 1);
  const r = pub.reviews[0];
  assert.deepEqual(Object.keys(r).sort(), ['again', 'id', 'name', 'photos', 'published_at', 'spot', 'swam_on', 'text']);
  assert.equal(r.again, true);
  assert.deepEqual(r.photos, [{ w: 1280, h: 960, tw: 320, th: 240 }]);
  const photo = await get(env, `/photos/${id}-0.jpg`);
  assert.equal(photo.status, 200);
  assert.equal(photo.headers.get('Content-Type'), 'image/jpeg');
  assert.equal((await get(env, `/photos/${id}-1.jpg`)).status, 404);
  assert.equal((await post(env, '/admin/decide', { id: 'f'.repeat(20), action: 'publish' }, { origin: null, auth: ADMIN })).status, 404);
  assert.equal((await post(env, '/admin/decide', { id, action: 'hide' }, { origin: null, auth: ADMIN })).status, 400);
});

test('a report reaches the queue; keeping the review clears it, deleting removes it all', async () => {
  const env = makeEnv();
  const { id } = await (await send(env, { ...REVIEW, consent: 'yes' }, [PHOTO])).json();
  // A review that is not published cannot be reported: nothing to see.
  assert.equal((await post(env, '/reviews/report', { id, reason: 'spam' })).status, 204);
  assert.equal(env.DB.rows('SELECT * FROM reports').length, 0);
  await publish(env, id);
  assert.equal((await post(env, '/reviews/report', { id, reason: 'not-a-reason' })).status, 400);
  assert.equal((await post(env, '/reviews/report', { id, reason: 'person' }, { origin: 'https://elsewhere.example' })).status, 403);
  assert.equal((await post(env, '/reviews/report', { id, reason: 'person' })).status, 204);
  assert.equal((await post(env, '/reviews/report', { id, reason: 'rude' }, { ip: '198.51.100.9' })).status, 204);
  let q = await (await get(env, '/admin/queue', ADMIN)).json();
  assert.deepEqual(q.reported.map((r) => r.id), [id]);
  assert.deepEqual(q.reported[0].reasons.sort(), ['person', 'rude']);
  assert.equal((await post(env, '/admin/decide', { id, action: 'keep' }, { origin: null, auth: ADMIN })).status, 204);
  q = await (await get(env, '/admin/queue', ADMIN)).json();
  assert.deepEqual(q.reported, []);
  assert.deepEqual(q.published.map((r) => r.id), [id]);
  await post(env, '/reviews/report', { id, reason: 'spam' });
  assert.equal((await post(env, '/admin/decide', { id, action: 'delete' }, { origin: null, auth: ADMIN })).status, 204);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 0);
  assert.equal(env.DB.rows('SELECT * FROM reports').length, 0);
  assert.equal(env.PHOTOS.map.size, 0);
});

test('the sender deletes their own review with its key, and only with it', async () => {
  const env = makeEnv();
  const { id, token } = await (await send(env, { ...REVIEW, consent: 'yes' }, [PHOTO])).json();
  assert.equal((await post(env, '/reviews/delete', { id, token: 'not-the-key' })).status, 403);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 1);
  assert.equal((await post(env, '/reviews/delete', { id, token })).status, 204);
  assert.equal(env.DB.rows('SELECT * FROM reviews').length, 0);
  assert.equal(env.PHOTOS.map.size, 0);
  assert.equal((await post(env, '/reviews/delete', { id, token })).status, 204, 'already gone is what was asked for');
  assert.equal((await post(env, '/reviews/delete', { id: 'nope', token })).status, 400);
});

test('the moderation page allows no inline script, and its own script is served', async () => {
  const env = makeEnv();
  const page = await get(env, '/moderate');
  assert.equal(page.status, 200);
  const csp = page.headers.get('Content-Security-Policy');
  assert.match(csp, /script-src 'self'(;|$)/);
  assert.ok(!csp.includes('unsafe-inline'));
  const html = await page.text();
  assert.ok(html.includes('<script src="/moderate.js"></script>') && !/<script>/.test(html));
  assert.ok(html.includes('href="https://swimsignal.co.uk/page.css"') && html.includes('data-site="https://swimsignal.co.uk/"'));
  const js = await get(env, '/moderate.js');
  assert.match(js.headers.get('Content-Type'), /javascript/);
  const source = await js.text();
  assert.ok(!source.includes('innerHTML'), 'review text is only ever set as text');
  new Function(source);   // it parses
  assert.equal((await get(env, '/')).status, 404);
});

test('the main module exports nothing but functions: the Workers runtime will not start otherwise', async () => {
  // workerd reads every named export of the main module as an entry point; a number, an array or an
  // object there stops the Worker at startup ("Incorrect type for map entry"), which Node never shows.
  const mod = await import('../src/index.js');
  for (const [name, value] of Object.entries(mod)) {
    if (name !== 'default') assert.equal(typeof value, 'function', name);
  }
  assert.equal(typeof mod.default.fetch, 'function');
  assert.equal(typeof mod.default.scheduled, 'function');
});
