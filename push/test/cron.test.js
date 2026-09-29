import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runCron, subKey } from '../src/index.js';
import { FakeKV, makeUserAgent, makeVapidEnv } from './helpers.js';

const SITE = 'https://ethanbuckley.github.io/dipcast/';
const vapid = await makeVapidEnv();

const spot = (id, rank, name = `Spot ${id}`) =>
  [id, { name, rank, level: ['low', 'moderate', 'high', 'very high'][rank] ?? null, headline: `${name} headline`, url: `${SITE}spot/${id}/` }];
const alertsJson = (generated_at, spots) => ({ generated_at, spots: Object.fromEntries(spots) });

// A KV holding the given state and subscribers, and a network that serves alerts.json
// and plays every push service, decrypting what it receives as the browser would.
async function setup({ state, users = {}, status = () => 201 }) {
  const kv = new FakeKV();
  if (state) await kv.put('state', JSON.stringify(state));
  const agents = {};
  for (const [name, spots] of Object.entries(users)) {
    const ua = makeUserAgent(`https://fcm.googleapis.com/fcm/send/${name}`);
    agents[ua.subscription.endpoint] = { name, ...ua };
    await kv.put(await subKey(ua.subscription.endpoint), JSON.stringify({ subscription: ua.subscription, spots, updated: 'x' }));
  }
  kv.writes = 0;
  const pushes = [];
  const alertFetches = [];
  let alerts;
  const fetch = async (url, init) => {
    if (url === `${SITE}data/alerts.json`) { alertFetches.push(init); return Response.json(alerts); }
    const agent = agents[url];
    pushes.push({ to: agent.name, headers: init.headers, payload: agent.read(init.body) });
    return new Response(status(agent.name) === 201 ? null : 'gone', { status: status(agent.name) });
  };
  const logs = [];
  const env = { PUSH: kv, SITE_URL: SITE, ...vapid };
  const run = (a) => { alerts = a; return runCron(env, { fetch, log: (m) => logs.push(m) }); };
  return { kv, env, run, pushes, logs, alertFetches, keyOf: (name) => subKey(`https://fcm.googleapis.com/fcm/send/${name}`) };
}

test('first run saves state and sends nothing', async () => {
  const t = await setup({ users: { ann: ['a'] } });
  await t.run(alertsJson('T1', [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.deepEqual(await t.kv.get('state', 'json'), { generated_at: 'T1', ranks: { a: 3 } });
  assert.equal(t.alertFetches[0].cache, 'no-store');
  assert.ok(t.alertFetches[0].signal instanceof AbortSignal);
});

test('unchanged generated_at does nothing', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 0 } }, users: { ann: ['a'] } });
  await t.run(alertsJson('T1', [spot('a', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.equal(t.kv.writes, 0);
});

test('a spot rising from 1 to 2 pushes once to each of its subscribers and nobody else', async () => {
  const t = await setup({
    state: { generated_at: 'T1', ranks: { a: 1, c: 0 } },
    users: { ann: ['a'], bob: ['c', 'a'], cat: ['c'], dan: ['z'] },
  });
  const result = await t.run(alertsJson('T2', [spot('a', 2, 'Pangbourne Meadow, River Thames'), spot('c', 1)]));
  assert.deepEqual(result, { risen: ['a'], sent: 2, removed: 0, failed: 0 });
  assert.deepEqual(t.pushes.map((p) => p.to).sort(), ['ann', 'bob']);
  for (const p of t.pushes) {
    assert.deepEqual(p.payload, {
      title: 'Pangbourne Meadow, River Thames',
      body: 'Pangbourne Meadow, River Thames headline',
      url: `${SITE}spot/a/`,
      tag: 'dipspot-a',
    });
    assert.equal(p.headers.TTL, '43200');
    assert.equal(p.headers.Urgency, 'normal');
    assert.equal(p.headers['Content-Encoding'], 'aes128gcm');
    assert.equal(p.headers['Content-Type'], 'application/octet-stream');
    assert.match(p.headers.Authorization, new RegExp(`^vapid t=[\\w-]+\\.[\\w-]+\\.[\\w-]+, k=${vapid.VAPID_PUBLIC_KEY}$`));
  }
  const state = await t.kv.get('state', 'json');
  assert.deepEqual([state.generated_at, state.ranks, Object.keys(state.alerted)], ['T2', { a: 2, c: 1 }, ['a']]);
});

test('a spot that drops back and rises again within 20 hours alerts once', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 1 } }, users: { ann: ['a'] } });
  let clock = Date.parse('2026-09-30T08:00:00Z');
  const run = (a) => runCron(t.env, { fetch: async (url, init) => url.endsWith('alerts.json') ? Response.json(a) : (t.pushes.push(url), new Response(null, { status: 201 })), log: () => {}, now: () => clock });
  await run(alertsJson('T2', [spot('a', 2)]));
  clock += 3 * 3600e3; await run(alertsJson('T3', [spot('a', 1)]));
  clock += 3 * 3600e3; await run(alertsJson('T4', [spot('a', 2)]));
  assert.equal(t.pushes.length, 1);
  clock += 21 * 3600e3; await run(alertsJson('T5', [spot('a', 1)]));
  await run(alertsJson('T6', [spot('a', 3)]));
  assert.equal(t.pushes.length, 2);
});

test('two spots rising for one subscriber make one combined push', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 0, b: 1 } }, users: { ann: ['a', 'b', 'c'] } });
  await t.run(alertsJson('T2', [spot('a', 2, 'Aston'), spot('b', 3, 'Bray'), spot('c', 0)]));
  assert.equal(t.pushes.length, 1);
  assert.deepEqual(t.pushes[0].payload, { title: '2 of your saved spots are high', body: 'Aston, Bray', url: `${SITE}saved/`, tag: 'dipspot-saved' });
});

test('a combined body is cut to 200 characters', async () => {
  const ids = Array.from({ length: 12 }, (_, i) => `s${i}`);
  const t = await setup({ state: { generated_at: 'T1', ranks: {} }, users: { ann: ids } });
  await t.run(alertsJson('T2', ids.map((id) => spot(id, 2, `A fairly long swim spot name ${id}`))));
  const { payload } = t.pushes[0];
  assert.equal(payload.title, '12 of your saved spots are high');
  assert.equal(payload.body.length, 200);
  assert.ok(payload.body.startsWith('A fairly long swim spot name s0, A fairly long swim spot name s1'));
});

test('a spot new since the last state counts as risen when high', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: {} }, users: { ann: ['new'] } });
  await t.run(alertsJson('T2', [spot('new', 2)]));
  assert.equal(t.pushes.length, 1);
});

test('404 and 410 delete the subscription; other failures are logged and kept', async () => {
  const codes = { ann: 410, bob: 404, cat: 500, dan: 201 };
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'], cat: ['a'], dan: ['a'] }, status: (n) => codes[n] });
  const result = await t.run(alertsJson('T2', [spot('a', 2)]));
  assert.deepEqual(result, { risen: ['a'], sent: 1, removed: 2, failed: 1 });
  assert.equal(await t.kv.get(await t.keyOf('ann')), null);
  assert.equal(await t.kv.get(await t.keyOf('bob')), null);
  assert.notEqual(await t.kv.get(await t.keyOf('cat')), null);
  assert.notEqual(await t.kv.get(await t.keyOf('dan')), null);
  assert.ok(t.logs.some((l) => /HTTP 500/.test(l)));
  assert.ok(t.logs.every((l) => !l.includes('fcm/send')), 'logs never contain an endpoint');
  assert.equal((await t.kv.get('state', 'json')).generated_at, 'T2');
});

test('a spot falling from 3 to 1, or staying high, sends nothing', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 3, b: 2 } }, users: { ann: ['a', 'b'] } });
  await t.run(alertsJson('T2', [spot('a', 1), spot('b', 3)]));
  assert.equal(t.pushes.length, 0);
  assert.deepEqual(await t.kv.get('state', 'json'), { generated_at: 'T2', ranks: { a: 1, b: 3 } });
});

test('a missing rank counts as no level, and a broken record does not stop the run', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 0 } }, users: { ann: ['a'], bob: ['a'] } });
  await t.kv.put('sub:broken', JSON.stringify({ subscription: { endpoint: 'https://fcm.googleapis.com/fcm/send/x', keys: {} }, spots: ['a'] }));
  const result = await t.run(alertsJson('T2', [spot('a', 2), ['b', { name: 'B' }]]));
  assert.equal(result.sent, 2);
  assert.equal(result.failed, 1);
  assert.equal((await t.kv.get('state', 'json')).ranks.b, -1);
});

test('a stored subscription on a host no longer allowed is deleted, not sent to', async () => {
  const t = await setup({ state: { generated_at: 'T1', ranks: { a: 0 } }, users: { ann: ['a'] } });
  const old = { ...makeUserAgent('https://push.example.com/old').subscription };
  await t.kv.put('sub:old', JSON.stringify({ subscription: old, spots: ['a'] }));
  await t.kv.put('sub:idle', JSON.stringify({ subscription: { ...old, endpoint: 'https://example.com/idle' }, spots: ['z'] }));
  const result = await t.run(alertsJson('T2', [spot('a', 2)]));
  assert.deepEqual(result, { risen: ['a'], sent: 1, removed: 2, failed: 0 });
  assert.deepEqual(t.pushes.map((p) => p.to), ['ann']);
  assert.equal(await t.kv.get('sub:old'), null);
  assert.equal(await t.kv.get('sub:idle'), null);
});

test('a failed alerts.json fetch throws and leaves state alone', async () => {
  const kv = new FakeKV();
  await kv.put('state', JSON.stringify({ generated_at: 'T1', ranks: {} }));
  const fetch = async () => new Response('nope', { status: 503 });
  await assert.rejects(runCron({ PUSH: kv, SITE_URL: SITE, ...vapid }, { fetch, log: () => {} }), /HTTP 503/);
  assert.equal((await kv.get('state', 'json')).generated_at, 'T1');
});
