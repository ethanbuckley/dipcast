import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runCron, subKey, forecastExpiry } from '../src/index.js';
import { FakeKV, makeUserAgent, makeVapidEnv } from './helpers.js';

const SITE = 'https://ethanbuckley.github.io/dipcast/';
const vapid = await makeVapidEnv();
const T1 = '2026-09-30T07:00:00.000Z', T2 = '2026-09-30T08:00:00.000Z';
const T3 = '2026-09-30T08:01:00.000Z', T4 = '2026-09-30T08:02:00.000Z';
const T5 = '2026-09-30T08:03:00.000Z', T6 = '2026-09-30T08:04:00.000Z';

const spot = (id, rank, name = `Spot ${id}`) =>
  [id, { name, rank, level: ['low', 'moderate', 'high', 'very high'][rank] ?? null, headline: `${name} headline`, url: `${SITE}spot/${id}/` }];
const alertsJson = (generated_at, spots) => ({ generated_at, spots: Object.fromEntries(spots) });

// A KV holding the given state and subscribers, and a network that serves alerts.json
// and plays every push service, decrypting what it receives as the browser would.
async function setup({ state, users = {}, status = () => 201, meta = false }) {
  const kv = new FakeKV();
  if (state) await kv.put('state', JSON.stringify(state));
  const agents = {};
  for (const [name, spots] of Object.entries(users)) {
    const ua = makeUserAgent(`https://fcm.googleapis.com/fcm/send/${name}`);
    agents[ua.subscription.endpoint] = { name, ...ua };
    await kv.put(await subKey(ua.subscription.endpoint), JSON.stringify({ subscription: ua.subscription, spots, updated: 'x' }), meta ? { metadata: { s: spots } } : {});
  }
  kv.writes = 0; kv.gets = 0;
  const pushes = [];
  const alertFetches = [];
  let alerts, clock = Date.parse('2026-09-30T08:10:00Z');
  const fetch = async (url, init) => {
    if (url.startsWith(`${SITE}data/alerts.json`)) { alertFetches.push({ url, ...init }); return Response.json(alerts); }
    const agent = agents[url];
    const rawPayload = agent.read(init.body);
    const { issued_at, expires_at, ...payload } = rawPayload;
    pushes.push({ to: agent.name, headers: init.headers, payload, rawPayload });
    const code = status(agent.name);
    return code instanceof Response ? code : new Response(code === 201 ? null : 'gone', { status: code });
  };
  const logs = [];
  const env = { PUSH: kv, SITE_URL: SITE, ...vapid };
  const stage = (a) => { alerts = a; return runCron(env, { fetch, log: (m) => logs.push(m), now: () => clock }); };
  // Most cases describe delivery; advance through the new enqueue-only invocation first.
  const run = async (a) => {
    const first = await stage(a);
    if (first.risen.length && first.queued) {
      const delivered = await stage(a);
      return { ...delivered, risen: first.risen, removed: first.removed + delivered.removed };
    }
    return first;
  };
  return { advance: ms => { clock += ms; }, now: () => clock, kv, env, run, stage, pushes, logs, alertFetches, keyOf: (name) => subKey(`https://fcm.googleapis.com/fcm/send/${name}`) };
}

test('first run saves state and sends nothing', async () => {
  const t = await setup({ users: { ann: ['a'] } });
  await t.run(alertsJson(T1, [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.deepEqual(await t.kv.get('state', 'json'), { generated_at: T1, ranks: { a: 3 } });
  assert.match(t.alertFetches[0].url, /alerts\.json\?t=\d+$/);   // a new address each minute, past any cache
  assert.equal(t.alertFetches[0].cache, undefined);   // Workers accept 'cache' only with a recent compatibility date
  assert.ok(t.alertFetches[0].signal instanceof AbortSignal);
});

test('unchanged generated_at does nothing', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.run(alertsJson(T1, [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.equal(t.kv.writes, 0);
});

test('a spot rising from 1 to 2 pushes once to each of its subscribers and nobody else', async () => {
  const t = await setup({
    state: { generated_at: T1, ranks: { a: 1, c: 0 } },
    users: { ann: ['a'], bob: ['c', 'a'], cat: ['c'], dan: ['z'] },
  });
  const result = await t.run(alertsJson(T2, [spot('a', 2, 'Pangbourne Meadow, River Thames'), spot('c', 1)]));
  assert.deepEqual(result, { risen: ['a'], sent: 2, removed: 0, failed: 0, queued: 0 });
  assert.deepEqual(t.pushes.map((p) => p.to).sort(), ['ann', 'bob']);
  for (const p of t.pushes) {
    assert.deepEqual(p.payload, {
      title: 'Pangbourne Meadow, River Thames',
      body: 'Pangbourne Meadow, River Thames headline',
      url: `${SITE}spot/a/`,
      tag: 'dipspot-a',
    });
    assert.equal(p.headers.TTL, '1800');
    assert.equal(p.rawPayload.issued_at, T2);
    assert.equal(Date.parse(p.rawPayload.expires_at), t.now() + 1800e3);
    assert.equal(p.headers.Urgency, 'normal');
    assert.equal(p.headers['Content-Encoding'], 'aes128gcm');
    assert.equal(p.headers['Content-Type'], 'application/octet-stream');
    assert.match(p.headers.Authorization, new RegExp(`^vapid t=[\\w-]+\\.[\\w-]+\\.[\\w-]+, k=${vapid.VAPID_PUBLIC_KEY}$`));
  }
  const state = await t.kv.get('state', 'json');
  assert.deepEqual([state.generated_at, state.ranks, Object.keys(state.alerted)], [T2, { a: 2, c: 1 }, ['a']]);
});

test('a spot that drops back and rises again within 20 hours alerts once', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 1 } }, users: { ann: ['a'] } });
  let clock = Date.parse('2026-09-30T08:00:00Z');
  const run = (a) => runCron(t.env, { fetch: async (url, init) => url.includes('alerts.json') ? Response.json({ ...a, generated_at: new Date(clock).toISOString() }) : (t.pushes.push(url), new Response(null, { status: 201 })), log: () => {}, now: () => clock });
  await run(alertsJson(T2, [spot('a', 2)]));
  await run(alertsJson(T2, [spot('a', 2)]));
  clock += 3 * 3600e3; await run(alertsJson(T3, [spot('a', 1)]));
  clock += 3 * 3600e3; await run(alertsJson(T4, [spot('a', 2)]));
  assert.equal(t.pushes.length, 1);
  clock += 21 * 3600e3; await run(alertsJson(T5, [spot('a', 1)]));
  clock += 60e3;
  await run(alertsJson(T6, [spot('a', 3)]));
  await run(alertsJson(T6, [spot('a', 3)]));
  assert.equal(t.pushes.length, 2);
});

test('two spots rising for one subscriber make one combined push', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0, b: 1 } }, users: { ann: ['a', 'b', 'c'] } });
  await t.run(alertsJson(T2, [spot('a', 2, 'Aston'), spot('b', 3, 'Bray'), spot('c', 0)]));
  assert.equal(t.pushes.length, 1);
  assert.deepEqual(t.pushes[0].payload, { title: '2 of your saved spots are high', body: 'Aston, Bray', url: `${SITE}saved/`, tag: 'dipspot-saved' });
});

test('a combined body is cut to 200 characters', async () => {
  const ids = Array.from({ length: 12 }, (_, i) => `s${i}`);
  const t = await setup({ state: { generated_at: T1, ranks: {} }, users: { ann: ids } });
  await t.run(alertsJson(T2, ids.map((id) => spot(id, 2, `A fairly long swim spot name ${id}`))));
  const { payload } = t.pushes[0];
  assert.equal(payload.title, '12 of your saved spots are high');
  assert.equal(payload.body.length, 200);
  assert.ok(payload.body.startsWith('A fairly long swim spot name s0, A fairly long swim spot name s1'));
});

test('a spot new since the last state counts as risen when high', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: {} }, users: { ann: ['new'] } });
  await t.run(alertsJson(T2, [spot('new', 2)]));
  assert.equal(t.pushes.length, 1);
});

test('404 and 410 delete the subscription; temporary failures remain queued', async () => {
  const codes = { ann: 410, bob: 404, cat: 500, dan: 201 };
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'], cat: ['a'], dan: ['a'] }, status: (n) => codes[n] });
  const result = await t.run(alertsJson(T2, [spot('a', 2)]));
  assert.deepEqual(result, { risen: ['a'], sent: 1, removed: 2, failed: 1, queued: 1 });
  assert.equal(await t.kv.get(await t.keyOf('ann')), null);
  assert.equal(await t.kv.get(await t.keyOf('bob')), null);
  assert.notEqual(await t.kv.get(await t.keyOf('cat')), null);
  assert.notEqual(await t.kv.get(await t.keyOf('dan')), null);
  assert.ok(t.logs.some((l) => /HTTP 500/.test(l)));
  assert.ok(t.logs.every((l) => !l.includes('fcm/send')), 'logs never contain an endpoint');
  assert.equal((await t.kv.get('state', 'json')).generated_at, T2);
});

test('a spot falling from 3 to 1, or staying high, sends nothing', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 3, b: 2 } }, users: { ann: ['a', 'b'] } });
  await t.run(alertsJson(T2, [spot('a', 1), spot('b', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.deepEqual(await t.kv.get('state', 'json'), { generated_at: T2, ranks: { a: 1, b: 3 } });
});

test('a missing rank counts as no level, and a broken record does not stop the run', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'] } });
  await t.kv.put('sub:broken', JSON.stringify({ subscription: { endpoint: 'https://fcm.googleapis.com/fcm/send/x', keys: {} }, spots: ['a'] }));
  const result = await t.run(alertsJson(T2, [spot('a', 2), ['b', { name: 'B' }]]));
  assert.equal(result.sent, 2);
  assert.equal(result.failed, 1);
  assert.equal((await t.kv.get('state', 'json')).ranks.b, -1);
});

test('a stored subscription on a host no longer allowed is deleted, not sent to', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  const old = { ...makeUserAgent('https://push.example.com/old').subscription };
  await t.kv.put('sub:old', JSON.stringify({ subscription: old, spots: ['a'] }));
  await t.kv.put('sub:idle', JSON.stringify({ subscription: { ...old, endpoint: 'https://example.com/idle' }, spots: ['z'] }));
  const result = await t.run(alertsJson(T2, [spot('a', 2)]));
  assert.deepEqual(result, { risen: ['a'], sent: 1, removed: 2, failed: 0, queued: 0 });
  assert.deepEqual(t.pushes.map((p) => p.to), ['ann']);
  assert.equal(await t.kv.get('sub:old'), null);
  assert.equal(await t.kv.get('sub:idle'), null);
});

test('a failed alerts.json fetch throws and leaves state alone', async () => {
  const kv = new FakeKV();
  await kv.put('state', JSON.stringify({ generated_at: T1, ranks: {} }));
  const fetch = async () => new Response('nope', { status: 503 });
  await assert.rejects(runCron({ PUSH: kv, SITE_URL: SITE, ...vapid }, { fetch, log: () => {} }), /HTTP 503/);
  assert.equal((await kv.get('state', 'json')).generated_at, T1);
});

test('queued batches recheck alerts.json before each delivery run', async () => {
  const users = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`u${String(i).padStart(2, '0')}`, ['a']]));
  const t = await setup({ state: { generated_at: T1, ranks: { a: 1 } }, users });
  const a = alertsJson(T2, [spot('a', 2)]);
  assert.deepEqual(await t.run(a), { risen: ['a'], sent: 15, removed: 0, failed: 0, queued: 25 });
  assert.deepEqual(await t.run(a), { risen: [], sent: 15, removed: 0, failed: 0, queued: 10 });
  assert.deepEqual(await t.run(a), { risen: [], sent: 10, removed: 0, failed: 0, queued: 0 });
  assert.equal(t.alertFetches.length, 4);   // staging and every batch revalidate the forecast
  assert.equal(await t.kv.get('queue'), null);
  assert.equal(new Set(t.pushes.map((p) => p.to)).size, 40);   // everyone, once
  assert.equal(t.pushes.length, 40);
  await t.run(a);   // queue empty: back to reading alerts.json, which has not changed
  assert.equal(t.alertFetches.length, 5);
  assert.equal(t.pushes.length, 40);
});

test('SENDS_PER_RUN raises the batch on a paid plan', async () => {
  const users = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`v${i}`, ['a']]));
  const t = await setup({ state: { generated_at: T1, ranks: { a: 1 } }, users });
  t.env.SENDS_PER_RUN = '100';
  assert.deepEqual(await t.run(alertsJson(T2, [spot('a', 2)])), { risen: ['a'], sent: 40, removed: 0, failed: 0, queued: 0 });
});

test('with the spots in each key\'s metadata, only the records being sent to are read', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 1 } }, meta: true,
    users: { ann: ['a'], bob: ['b'], cat: ['c'], dan: ['d'], eve: ['e'] } });
  await t.run(alertsJson(T2, [spot('a', 2)]));
  assert.deepEqual(t.pushes.map((p) => p.to), ['ann']);
  assert.equal(t.kv.gets, 5);   // queue/state on staging and draining, then ann; no other subscribers
});

test('a failed queue write leaves the previous ranks available for rediscovery', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`u${i}`, ['a']])) });
  const put = t.kv.put.bind(t.kv);
  t.kv.put = async (key, ...args) => { if (key === 'queue') throw new Error('queue unavailable'); return put(key, ...args); };
  const a = alertsJson(T2, [spot('a', 2)]);
  await assert.rejects(t.stage(a), /queue unavailable/);
  assert.equal((await t.kv.get('state', 'json')).generated_at, T1);
  assert.equal(t.pushes.length, 0);
  t.kv.put = put;
  await t.run(a);
  await t.stage(a);
  assert.equal(t.pushes.length, 20);
});

test('a persisted queue recovers a failed state write before sending', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  const put = t.kv.put.bind(t.kv);
  t.kv.put = async (key, ...args) => { if (key === 'state') throw new Error('state unavailable'); return put(key, ...args); };
  const a = alertsJson(T2, [spot('a', 2)]);
  await assert.rejects(t.stage(a), /state unavailable/);
  assert.equal((await t.kv.get('queue', 'json')).items.length, 1);
  await assert.rejects(t.stage(a), /state unavailable/);
  assert.equal(t.pushes.length, 0);
  t.kv.put = put;
  await t.stage(a);
  assert.equal(t.pushes.length, 1);
  assert.equal((await t.kv.get('state', 'json')).generated_at, T2);
  await t.stage(a);
  assert.equal(t.pushes.length, 1);
});

test('an interrupted delivery checkpoint retains every unsent recipient', async () => {
  const users = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`u${i}`, ['a']]));
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users });
  const a = alertsJson(T2, [spot('a', 2)]);
  await t.stage(a);
  const put = t.kv.put.bind(t.kv);
  t.kv.put = async (key, ...args) => { if (key === 'queue') throw new Error('checkpoint interrupted'); return put(key, ...args); };
  await assert.rejects(t.stage(a), /checkpoint interrupted/);
  assert.equal((await t.kv.get('queue', 'json')).items.length, 20);
  t.kv.put = put;
  await t.stage(a);
  await t.stage(a);
  assert.equal(new Set(t.pushes.map(p => p.to)).size, 20);
  // Some sends may repeat after a crash; this is recovery, not exactly-once delivery.
  assert.equal(t.pushes.length, 35);
});

test('a queued spot removed from Saved is not delivered', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a', 'b'], bob: ['a', 'b'] } });
  t.env.SENDS_PER_RUN = '1';
  const a = alertsJson(T2, [spot('a', 2)]);
  await t.stage(a);
  const key = await t.keyOf('ann'), record = await t.kv.get(key, 'json');
  await t.kv.put(key, JSON.stringify({ ...record, spots: ['b'] }));
  await t.stage(a);
  await t.stage(a);
  assert.deepEqual(t.pushes.map(p => p.to), ['bob']);
  assert.equal(await t.kv.get('queue'), null);
});

test('a combined queued alert is rebuilt for only the spots still saved', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0, b: 0 } }, users: { ann: ['a', 'b'], bob: ['a', 'b'] } });
  t.env.SENDS_PER_RUN = '1';
  const a = alertsJson(T2, [spot('a', 2, 'Aston'), spot('b', 3, 'Bray')]);
  await t.stage(a);
  const key = await t.keyOf('ann'), record = await t.kv.get(key, 'json');
  await t.kv.put(key, JSON.stringify({ ...record, spots: ['b', 'new'] }));
  await t.stage(a);
  await t.stage(a);
  assert.deepEqual(t.pushes.find(p => p.to === 'ann').payload, { title: 'Bray', body: 'Bray headline', url: `${SITE}spot/b/`, tag: 'dipspot-b' });
});

test('turning alerts off while queued prevents delivery', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  const a = alertsJson(T2, [spot('a', 2)]);
  await t.stage(a);
  await t.kv.delete(await t.keyOf('ann'));
  await t.stage(a);
  assert.equal(t.pushes.length, 0);
});

test('staging and draining never write the queue twice in the same invocation', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'] } });
  t.env.SENDS_PER_RUN = '1';
  const put = t.kv.put.bind(t.kv), del = t.kv.delete.bind(t.kv);
  let writes = new Set();
  const check = key => { assert.ok(!writes.has(key), `repeat write to ${key}`); writes.add(key); };
  t.kv.put = async (key, ...args) => { check(key); return put(key, ...args); };
  t.kv.delete = async key => { check(key); return del(key); };
  const a = alertsJson(T2, [spot('a', 2)]);
  assert.deepEqual(await t.stage(a), { risen: ['a'], sent: 0, removed: 0, failed: 0, queued: 2 });
  assert.equal(t.pushes.length, 0);
  writes = new Set(); await t.stage(a);
  writes = new Set(); await t.stage(a);
  assert.equal(t.pushes.length, 2);
});

test('legacy combined queued alerts are discarded when saved-spot membership cannot be checked', async () => {
  const t = await setup({ users: { ann: ['b'] } });
  await t.kv.put('queue', JSON.stringify({ created_at: t.now(), items: [{ key: await t.keyOf('ann'), payload: { title: '2 spots high', tag: 'dipspot-saved' } }] }));
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  assert.equal(t.pushes.length, 0);
  assert.equal(await t.kv.get('queue'), null);
});

test('legacy single-spot queued alerts are delivered only if still saved', async () => {
  const t = await setup({ users: { ann: ['a'], bob: ['b'] } });
  const payload = { title: 'Aston', tag: 'dipspot-a' };
  await t.kv.put('queue', JSON.stringify({ created_at: t.now(), items: await Promise.all(['ann', 'bob'].map(async name => ({ key: await t.keyOf(name), payload }))) }));
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  assert.deepEqual(t.pushes.map(p => p.to), ['ann']);
});

test('temporary push failures retry after backoff, without repeating successful recipients', async () => {
  let healthy = false;
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'] }, status: name => name === 'ann' && !healthy ? 503 : 201 });
  const a = alertsJson(T2, [spot('a', 2)]);
  assert.equal((await t.run(a)).queued, 1);
  await t.stage(a);
  assert.equal(t.pushes.length, 2);
  t.advance(120000); healthy = true;
  assert.equal((await t.stage(a)).queued, 0);
  assert.deepEqual(t.pushes.map(p => p.to).sort(), ['ann', 'ann', 'bob']);
});

test('429 honours Retry-After seconds and HTTP dates', async () => {
  for (const date of [false, true]) {
    let header, healthy = false;
    const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] }, status: () => healthy ? 201 : new Response(null, { status: 429, headers: { 'Retry-After': header } }) });
    header = date ? new Date(t.now() + 10 * 60e3).toUTCString() : '600';
    const a = alertsJson(T2, [spot('a', 2)]);
    await t.run(a); t.advance(9 * 60e3); await t.stage(a);
    assert.equal(t.pushes.length, 1);
    healthy = true; t.advance(60e3); await t.stage(a);
    assert.equal(t.pushes.length, 2);
  }
});

test('retries stop after four attempts and do not log endpoint-bearing exceptions', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] }, status: () => { throw new Error('https://fcm.googleapis.com/fcm/send/secret'); } });
  const a = alertsJson(T2, [spot('a', 2)]);
  await t.run(a);
  for (const minutes of [2, 4, 8]) { t.advance(minutes * 60e3); await t.stage(a); }
  assert.equal(t.pushes.length, 4);
  assert.equal(await t.kv.get('queue'), null);
  assert.ok(t.logs.every(line => !line.includes('secret') && !line.includes('fcm.googleapis.com')));
});

test('permanent 400 and 403 errors are not retried', async () => {
  for (const code of [400, 403]) {
    const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] }, status: () => code });
    await t.run(alertsJson(T2, [spot('a', 2)]));
    assert.equal(await t.kv.get('queue'), null);
    assert.notEqual(await t.kv.get(await t.keyOf('ann')), null);
  }
});

test('a newer lower forecast cancels a pending retry', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] }, status: () => 503 });
  await t.run(alertsJson(T2, [spot('a', 2)]));
  t.advance(120000);
  await t.stage(alertsJson(T3, [spot('a', 0)]));
  assert.equal(t.pushes.length, 1);
  assert.equal(await t.kv.get('queue'), null);
});

test('newer forecast headlines replace queued wording', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.stage(alertsJson(T2, [spot('a', 2, 'Old')]));
  await t.stage(alertsJson(T3, [spot('a', 3, 'Current')]));
  assert.equal(t.pushes[0].payload.title, 'Current');
  assert.equal(t.pushes[0].rawPayload.issued_at, T3);
});

test('a queue expires after 30 minutes even if fresh forecasts stay high', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  t.advance(30 * 60e3);
  await t.stage(alertsJson(T3, [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.equal(await t.kv.get('queue'), null);
});

test('an expired feed sends nothing and does not advance the comparison state', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  t.advance(8 * 3600e3);
  await t.stage(alertsJson(T2, [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.equal((await t.kv.get('state', 'json')).generated_at, T1);
});

test('malformed, future and regressed forecast times are refused', async () => {
  for (const stamp of ['invalid', '2026-10-01T08:00:00Z', '2026-09-30T06:59:00Z']) {
    const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
    await assert.rejects(t.stage(alertsJson(stamp, [spot('a', 2)])), /issue time/);
    assert.equal(t.pushes.length, 0);
    assert.equal((await t.kv.get('state', 'json')).generated_at, T1);
  }
});

test('a failed forecast refresh retains the pending queue without sending blindly', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  await assert.rejects(runCron(t.env, { now: t.now, fetch: async () => new Response(null, { status: 503 }), log: () => {} }), /HTTP 503/);
  assert.equal((await t.kv.get('queue', 'json')).items.length, 1);
  assert.equal(t.pushes.length, 0);
});

test('expiry is capped at London midnight, including DST transition dates', () => {
  for (const [issued, end] of [
    ['2026-09-30T22:50:00Z', '2026-09-30T23:00:00Z'],
    ['2026-12-30T23:50:00Z', '2026-12-31T00:00:00Z'],
    ['2026-03-28T23:50:00Z', '2026-03-29T00:00:00Z'],
    ['2026-10-24T22:50:00Z', '2026-10-24T23:00:00Z'],
    ['2026-09-30T08:00:00Z', '2026-09-30T16:00:00Z'],
  ]) assert.equal(forecastExpiry(issued), Date.parse(end));
});

test('the push TTL shrinks with the queue lifetime', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  t.advance(29 * 60e3);
  await t.stage(alertsJson(T2, [spot('a', 2)]));
  assert.equal(t.pushes[0].headers.TTL, '60');
});

test('a temporary subscription read failure retains the recipient for retry', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] } });
  const a = alertsJson(T2, [spot('a', 2)]);
  await t.stage(a);
  const key = await t.keyOf('ann'), get = t.kv.get.bind(t.kv);
  t.kv.get = async (name, ...args) => { if (name === key) throw new Error('temporary'); return get(name, ...args); };
  assert.equal((await t.stage(a)).queued, 1);
  assert.equal(t.pushes.length, 0);
  t.kv.get = get; t.advance(120000);
  await t.stage(a);
  assert.equal(t.pushes.length, 1);
});

test('Retry-After beyond expiry is dropped rather than sent too early', async () => {
  const t = await setup({ state: { generated_at: T1, ranks: { a: 0 } }, users: { ann: ['a'] }, status: () => new Response(null, { status: 429, headers: { 'Retry-After': '3600' } }) });
  await t.run(alertsJson(T2, [spot('a', 2)]));
  assert.equal(await t.kv.get('queue'), null);
  assert.equal(t.pushes.length, 1);
});
