// Dipspot push alerts. The site's pages subscribe here; every 30 minutes the cron
// compares the site's alerts.json with the last run and pushes to people whose saved
// spots have just become high.

import { concat, importEcdhPublic, sendPush, unb64url, vapidSigner } from './webpush.js';

const MAX_BODY = 8 * 1024;
const MAX_SPOTS = 100;
const SPOT_ID = /^[A-Za-z0-9_-]{1,80}$/;
const HIGH = 2;
// At most one alert a spot in 20 hours: the site is rebuilt several times a day, and a spot near
// the line can cross it, drop back and cross again, which would alert people each time.
const QUIET_MS = 20 * 3600 * 1000;
const CONCURRENCY = 6; // Workers allow six open connections per invocation.
// The browsers' push services. Accepting any https URL would let anyone make the Worker
// POST to a host of their choosing.
const PUSH_HOSTS = ['fcm.googleapis.com', 'updates.push.services.mozilla.com', 'web.push.apple.com'];
const PUSH_SUFFIXES = ['.push.apple.com', '.notify.windows.com'];

export default {
  fetch: (request, env) => handleRequest(request, env),
  async scheduled(controller, env) { await runCron(env); },
};

// ---- HTTP ----

class Invalid extends Error {}
const check = (ok, reason) => { if (!ok) throw new Invalid(reason); };
const isObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);
const reply = (status, text, headers = {}) =>
  new Response(text, { status, headers: { 'Content-Type': 'text/plain; charset=utf-8', ...headers } });

const ROUTES = new Map([['/subscribe', subscribe], ['/unsubscribe', unsubscribe]]);

export async function handleRequest(request, env) {
  const route = ROUTES.get(new URL(request.url).pathname);
  if (!route) return reply(404, 'Not found');
  if (request.method !== 'POST' && request.method !== 'OPTIONS') return reply(405, 'Method not allowed', { Allow: 'POST, OPTIONS' });
  // Browsers always send Origin on these requests, so a missing one is not the site either.
  if (request.headers.get('Origin') !== env.ALLOWED_ORIGIN) return reply(403, 'Origin not allowed');
  const cors = { 'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN, Vary: 'Origin' };
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: {
      ...cors,
      'Access-Control-Allow-Methods': 'POST',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    } });
  }
  try {
    await route(await readJson(request), env);
    return new Response(null, { status: 204, headers: cors });
  } catch (err) {
    if (err instanceof Invalid) return reply(400, err.message, cors);
    throw err;
  }
}

// Reads at most MAX_BODY bytes, so an oversized body is refused without buffering it.
async function readJson(request) {
  check(!(Number(request.headers.get('Content-Length')) > MAX_BODY), 'Body too large');
  const chunks = [];
  let size = 0;
  const reader = request.body?.getReader();
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) { await reader.cancel(); throw new Invalid('Body too large'); }
    chunks.push(value);
  }
  const text = new TextDecoder().decode(concat(...chunks));
  try { return JSON.parse(text); } catch { throw new Invalid('Body is not JSON'); }
}

function checkEndpoint(endpoint) {
  check(typeof endpoint === 'string' && endpoint.length < 1024, 'endpoint must be a string under 1024 characters');
  let url;
  try { url = new URL(endpoint); } catch { throw new Invalid('endpoint is not a URL'); }
  check(url.protocol === 'https:', 'endpoint must be https');
  return endpoint;
}

export function isPushService(endpoint) {
  let url;
  try { url = new URL(endpoint); } catch { return false; }
  const host = url.hostname;
  return url.protocol === 'https:' && url.port === '' && !url.username && !url.password
    && (PUSH_HOSTS.includes(host) || PUSH_SUFFIXES.some((suffix) => host.endsWith(suffix)));
}

function decodeKey(value, name, bytes, maxChars) {
  check(typeof value === 'string' && value.length <= maxChars, `${name} has the wrong length`);
  let raw;
  try { raw = unb64url(value); } catch { throw new Invalid(`${name} is not base64url`); }
  check(raw.length === bytes, `${name} must decode to ${bytes} bytes`);
  return raw;
}

// Keeps only the fields push needs, so nothing else a client sends is ever stored.
async function cleanSubscription(sub) {
  check(isObject(sub), 'subscription missing');
  const endpoint = checkEndpoint(sub.endpoint);
  check(isPushService(endpoint), 'unsupported push service');
  const { expirationTime = null, keys } = sub;
  check(expirationTime === null || Number.isFinite(expirationTime), 'expirationTime must be a number or null');
  check(isObject(keys), 'subscription keys missing');
  const p256dh = decodeKey(keys.p256dh, 'p256dh', 65, 90);
  check(p256dh[0] === 4, 'p256dh must be an uncompressed P-256 point');
  // Catches points that are not on the curve now, rather than failing at every alert.
  try { await importEcdhPublic(p256dh); } catch { throw new Invalid('p256dh is not a P-256 point'); }
  decodeKey(keys.auth, 'auth', 16, 24);
  const strip = (s) => s.replace(/=+$/, '');
  return { endpoint, expirationTime, keys: { p256dh: strip(keys.p256dh), auth: strip(keys.auth) } };
}

function cleanSpots(spots) {
  check(Array.isArray(spots), 'spots must be an array');
  check(spots.length <= MAX_SPOTS, `at most ${MAX_SPOTS} spots`);
  check(spots.every((s) => typeof s === 'string' && SPOT_ID.test(s)), 'bad spot id');
  check(new Set(spots).size === spots.length, 'duplicate spot id');
  return spots;
}

export async function subKey(endpoint) {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint)));
  return 'sub:' + Array.from(hash, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function subscribe(data, env) {
  check(isObject(data), 'body must be a JSON object');
  const subscription = await cleanSubscription(data.subscription);
  const spots = cleanSpots(data.spots);
  const key = await subKey(subscription.endpoint);
  if (spots.length === 0) await env.PUSH.delete(key);
  else await env.PUSH.put(key, JSON.stringify({ subscription, spots, updated: new Date().toISOString() }));
}

async function unsubscribe(data, env) {
  check(isObject(data), 'body must be a JSON object');
  await env.PUSH.delete(await subKey(checkEndpoint(data.endpoint)));
}

// ---- Cron ----

export async function runCron(env, { fetch = globalThis.fetch, log = console.log, now = Date.now } = {}) {
  const authorize = vapidSigner(env); // fails on bad config every run, not only when a spot rises
  const res = await fetch(`${env.SITE_URL}data/alerts.json`, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
  if (!res.ok) throw new Error(`alerts.json: HTTP ${res.status}`);
  const alerts = await res.json();
  if (typeof alerts?.generated_at !== 'string' || !isObject(alerts.spots)) throw new Error('alerts.json: unexpected shape');

  const prev = await env.PUSH.get('state', 'json');
  if (prev?.generated_at === alerts.generated_at) {
    log(`cron: alerts.json unchanged (${alerts.generated_at})`);
    return { risen: [], sent: 0, removed: 0, failed: 0 };
  }
  const ranks = {};
  for (const [id, spot] of Object.entries(alerts.spots)) ranks[id] = Number.isInteger(spot?.rank) ? spot.rank : -1;

  let result = { risen: [], sent: 0, removed: 0, failed: 0 };
  const t = now(), alerted = Object.fromEntries(Object.entries(prev?.alerted ?? {}).filter(([, at]) => t - Date.parse(at) < QUIET_MS));
  if (!prev) {
    // A first run has nothing to compare with; alerting now would alert everyone.
    log(`cron: first run, saved ranks for ${Object.keys(ranks).length} spots, sent nothing`);
  } else {
    const risen = Object.keys(ranks).filter((id) => ranks[id] >= HIGH && (prev.ranks?.[id] ?? -1) < HIGH && !alerted[id]);
    for (const id of risen) alerted[id] = new Date(t).toISOString();
    if (risen.length) result = { risen, ...await notify(env, alerts, new Set(risen), authorize, { fetch, log }) };
    log(`cron: ${alerts.generated_at}: ${risen.length} spots rose to high; sent ${result.sent}, removed ${result.removed}, failed ${result.failed}`);
  }
  await env.PUSH.put('state', JSON.stringify({ generated_at: alerts.generated_at, ranks, ...(Object.keys(alerted).length ? { alerted } : {}) }));
  return result;
}

async function listKeys(kv, prefix) {
  const names = [];
  let cursor;
  do {
    const page = await kv.list({ prefix, cursor });
    names.push(...page.keys.map((k) => k.name));
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return names;
}

async function notify(env, alerts, risen, authorize, { fetch, log }) {
  const tally = { sent: 0, removed: 0, failed: 0 };
  // Logs name a subscription by a slice of its key hash; the endpoint itself is a secret.
  const fail = (key, why) => { tally.failed++; log(`push ${key.slice(4, 16)}: ${why}`); };
  await eachLimit(await listKeys(env.PUSH, 'sub:'), CONCURRENCY, async (key) => {
    try {
      const record = await env.PUSH.get(key, 'json');
      if (!record) return;
      if (!isPushService(record.subscription?.endpoint)) {
        await env.PUSH.delete(key); // stored before its host was dropped from the list
        tally.removed++;
        log(`push ${key.slice(4, 16)}: removed, push service not allowed`);
        return;
      }
      const hits = (record.spots ?? []).filter((id) => risen.has(id));
      if (!hits.length) return;
      const res = await sendPush(record.subscription, alertPayload(hits, alerts.spots, env.SITE_URL), authorize, { fetch });
      if (res.status === 404 || res.status === 410) {
        await res.body?.cancel();
        await env.PUSH.delete(key); // the browser has dropped this subscription
        tally.removed++;
      } else if (res.ok) {
        await res.body?.cancel(); // release the connection; only the status matters
        tally.sent++;
      } else {
        fail(key, `HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
      }
    } catch (err) {
      fail(key, err.message);
    }
  });
  return tally;
}

async function eachLimit(items, limit, fn) {
  let next = 0;
  const lane = async () => { while (next < items.length) await fn(items[next++]); };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
}

export function alertPayload(ids, spots, siteUrl) {
  if (ids.length === 1) {
    const [id] = ids;
    const spot = spots[id];
    return { title: spot.name ?? id, body: spot.headline ?? '', url: spot.url ?? `${siteUrl}spot/${id}/`, tag: `dipspot-${id}` };
  }
  const names = ids.map((id) => spots[id].name ?? id).join(', ');
  return {
    title: `${ids.length} of your saved spots are high`,
    body: names.length > 200 ? names.slice(0, 199) + '…' : names,
    url: `${siteUrl}saved/`,
    tag: 'dipspot-saved',
  };
}
