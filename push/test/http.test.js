import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import worker, { subKey } from '../src/index.js';
import { FakeKV, makeUserAgent } from './helpers.js';

const ORIGIN = 'https://ethanbuckley.github.io';
const BASE = 'https://dipspot-push.example.workers.dev';
const makeEnv = () => ({ PUSH: new FakeKV(), ALLOWED_ORIGIN: ORIGIN });

const call = (env, path, { method = 'POST', origin = ORIGIN, body, headers = {} } = {}) =>
  worker.fetch(new Request(BASE + path, {
    method,
    headers: { ...(origin ? { Origin: origin } : {}), 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
  }), env);

const ua = makeUserAgent('https://fcm.googleapis.com/fcm/send/abc123');
const sub = ua.subscription;

test('subscribe stores {subscription, spots, updated} under sub:<sha-256 of endpoint>', async () => {
  const env = makeEnv();
  const res = await call(env, '/subscribe', { body: { subscription: { ...sub, extra: 'dropped' }, spots: ['bw-ukj1102-11941', 'b'] } });
  assert.equal(res.status, 204);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  const key = 'sub:' + createHash('sha256').update(sub.endpoint).digest('hex');
  assert.equal(await subKey(sub.endpoint), key);
  const stored = await env.PUSH.get(key, 'json');
  assert.deepEqual(stored.subscription, sub, 'only endpoint, expirationTime and keys are kept');
  assert.deepEqual(stored.spots, ['bw-ukj1102-11941', 'b']);
  assert.ok(!Number.isNaN(Date.parse(stored.updated)));
  assert.deepEqual([...env.PUSH.map.keys()], [key]);
});

test('subscribe with no spots deletes; unsubscribe deletes', async () => {
  const env = makeEnv();
  await call(env, '/subscribe', { body: { subscription: sub, spots: ['a'] } });
  assert.equal(env.PUSH.map.size, 1);
  assert.equal((await call(env, '/subscribe', { body: { subscription: sub, spots: [] } })).status, 204);
  assert.equal(env.PUSH.map.size, 0);

  await call(env, '/subscribe', { body: { subscription: sub, spots: ['a'] } });
  const res = await call(env, '/unsubscribe', { body: { endpoint: sub.endpoint } });
  assert.equal(res.status, 204);
  assert.equal(env.PUSH.map.size, 0);
});

test('CORS preflight from the allowed origin', async () => {
  const res = await call(makeEnv(), '/subscribe', { method: 'OPTIONS', headers: { 'Access-Control-Request-Method': 'POST' } });
  assert.equal(res.status, 204);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.equal(res.headers.get('Access-Control-Allow-Methods'), 'POST');
  assert.equal(res.headers.get('Access-Control-Allow-Headers'), 'Content-Type');
  assert.equal(res.headers.get('Access-Control-Max-Age'), '86400');
});

test('another origin, or none, gets 403 and nothing is stored', async () => {
  const env = makeEnv();
  for (const origin of ['https://evil.example', 'https://ethanbuckley.github.io.evil.example', null]) {
    assert.equal((await call(env, '/subscribe', { origin, body: { subscription: sub, spots: ['a'] } })).status, 403);
    assert.equal((await call(env, '/subscribe', { origin, method: 'OPTIONS' })).status, 403);
  }
  assert.equal(env.PUSH.writes, 0);
});

test('unknown path is 404, wrong method is 405', async () => {
  assert.equal((await call(makeEnv(), '/', { method: 'GET' })).status, 404);
  assert.equal((await call(makeEnv(), '/state', { body: {} })).status, 404);
  const res = await call(makeEnv(), '/subscribe', { method: 'GET' });
  assert.equal(res.status, 405);
  assert.equal(res.headers.get('Allow'), 'POST, OPTIONS');
});

test('validation failures are 400 with a reason, and store nothing', async () => {
  const keys = sub.keys;
  const point = Buffer.from(keys.p256dh, 'base64url');
  const withKeys = (k) => ({ subscription: { ...sub, keys: { ...keys, ...k } }, spots: ['a'] });
  const cases = {
    'not JSON': '{"subscription":',
    'array body': [],
    'no subscription': { spots: ['a'] },
    'http endpoint': { subscription: { ...sub, endpoint: 'http://push.example.net/x' }, spots: ['a'] },
    'endpoint not a URL': { subscription: { ...sub, endpoint: 'nonsense' }, spots: ['a'] },
    'endpoint 1024 chars': { subscription: { ...sub, endpoint: 'https://fcm.googleapis.com/' + 'x'.repeat(997) }, spots: ['a'] },
    'no keys': { subscription: { endpoint: sub.endpoint }, spots: ['a'] },
    'p256dh too short': withKeys({ p256dh: point.subarray(0, 64).toString('base64url') }),
    'p256dh compressed': withKeys({ p256dh: Buffer.concat([Buffer.from([2]), point.subarray(1)]).toString('base64url') }),
    'p256dh not on curve': withKeys({ p256dh: Buffer.concat([Buffer.from([4]), Buffer.alloc(64)]).toString('base64url') }),
    'p256dh not base64url': withKeys({ p256dh: keys.p256dh.replace(/.$/, '+') }),
    'auth 15 bytes': withKeys({ auth: Buffer.alloc(15).toString('base64url') }),
    'auth not a string': withKeys({ auth: 12 }),
    'expirationTime a string': { subscription: { ...sub, expirationTime: 'soon' }, spots: ['a'] },
    'spots not array': { subscription: sub, spots: 'a' },
    '101 spots': { subscription: sub, spots: Array.from({ length: 101 }, (_, i) => `s${i}`) },
    'duplicate spots': { subscription: sub, spots: ['a', 'a'] },
    'bad spot id': { subscription: sub, spots: ['a/b'] },
    'spot id 81 chars': { subscription: sub, spots: ['x'.repeat(81)] },
    'spot id not a string': { subscription: sub, spots: [7] },
  };
  const env = makeEnv();
  for (const [name, body] of Object.entries(cases)) {
    const res = await call(env, '/subscribe', { body });
    assert.equal(res.status, 400, name);
    assert.ok((await res.text()).length > 0, name);
    assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN, `${name}: the page can read the reason`);
  }
  assert.equal((await call(env, '/unsubscribe', { body: { endpoint: 'http://x.example' } })).status, 400);
  assert.equal((await call(env, '/unsubscribe', { body: {} })).status, 400);
  assert.equal(env.PUSH.writes, 0);
});

test('100 spots and an 80-char id are accepted; padded keys are stored unpadded', async () => {
  const env = makeEnv();
  const spots = Array.from({ length: 99 }, (_, i) => `s${i}`).concat('x'.repeat(80));
  const padded = { ...sub, keys: { p256dh: sub.keys.p256dh + '=', auth: sub.keys.auth + '==' } };
  assert.equal((await call(env, '/subscribe', { body: { subscription: padded, spots } })).status, 204);
  const [stored] = [...env.PUSH.map.values()].map((v) => JSON.parse(v));
  assert.deepEqual(stored.subscription.keys, sub.keys);
});

test('only the browsers\' push services are accepted as endpoints', async () => {
  const allowed = [
    'https://fcm.googleapis.com/fcm/send/abc',
    'https://updates.push.services.mozilla.com/wpush/v2/abc',
    'https://web.push.apple.com/QGx',
    'https://api.push.apple.com/3/device/abc',
    'https://wns2-par02p.notify.windows.com/w/?token=abc',
  ];
  const rejected = [
    'https://example.com/x',
    'https://fcm.googleapis.com.evil.example/x',
    'https://evilfcm.googleapis.com/x',
    'https://notify.windows.com.evil.example/x',
    'https://evil-push.apple.com/x',
    'https://push.apple.com/x',
    'https://fcm.googleapis.com:8443/x',
    'https://user@fcm.googleapis.com/x',
  ];
  for (const endpoint of allowed) {
    const env = makeEnv();
    const res = await call(env, '/subscribe', { body: { subscription: { ...sub, endpoint }, spots: ['a'] } });
    assert.equal(res.status, 204, endpoint);
    assert.equal(env.PUSH.map.size, 1, endpoint);
  }
  for (const endpoint of rejected) {
    const env = makeEnv();
    const res = await call(env, '/subscribe', { body: { subscription: { ...sub, endpoint }, spots: ['a'] } });
    assert.equal(res.status, 400, endpoint);
    assert.equal(await res.text(), 'unsupported push service', endpoint);
    assert.equal(env.PUSH.writes, 0, endpoint);
  }
});

test('a body over 8 KB is refused, with or without Content-Length', async () => {
  const env = makeEnv();
  const big = JSON.stringify({ subscription: sub, spots: ['a'], pad: 'x'.repeat(8200) });
  const res = await call(env, '/subscribe', { body: big });
  assert.equal(res.status, 400);
  assert.match(await res.text(), /too large/);

  // A streamed body has no Content-Length, so the size is counted while reading.
  const stream = new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(big)); c.close(); } });
  const req = new Request(BASE + '/subscribe', { method: 'POST', headers: { Origin: ORIGIN }, body: stream, duplex: 'half' });
  assert.equal(req.headers.get('Content-Length'), null);
  const streamed = await worker.fetch(req, env);
  assert.equal(streamed.status, 400);
  assert.match(await streamed.text(), /too large/);
  assert.equal(env.PUSH.writes, 0);
});
