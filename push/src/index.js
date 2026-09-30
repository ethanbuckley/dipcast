// Dipspot push alerts. The site's pages subscribe here; every 2 minutes the cron
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
  if (spots.length === 0) { await env.PUSH.delete(key); return; }
  // The spots also go in the key's metadata (at most 1024 bytes), so the cron finds who to alert
  // from a list of keys alone instead of reading every record.
  const meta = JSON.stringify(spots).length <= 1000 ? { metadata: { s: spots } } : {};
  await env.PUSH.put(key, JSON.stringify({ subscription, spots, updated: new Date().toISOString() }), meta);
}

async function unsubscribe(data, env) {
  check(isObject(data), 'body must be a JSON object');
  await env.PUSH.delete(await subKey(checkEndpoint(data.endpoint)));
}

// ---- Cron ----
// Every 2 minutes. A run reads alerts.json, then either sends the next batch of queued alerts,
// each checked against that forecast, or, with nothing queued, queues one alert for each
// subscriber of the spots that have just turned high. On the free plan a run gets 50 outgoing
// requests and 10 ms of CPU, and one notification took about 0.3 ms of CPU in Node, so a run
// sends at most SENDS_PER_RUN (15, or the env's; raise it on the Paid plan): about 450 an hour. KV can take a minute to show a write everywhere, and a
// repeat of a batch is silent (a notification with the same tag replaces the last one).
const SENDS_PER_RUN = 15;
const MAX_FORECAST_AGE = 8 * 3600e3; // same freshness limit as the site's warning
const MAX_ATTEMPTS = 4;
// A push service's Retry-After longer than this gives that alert up: while anything is queued, no
// new rise is looked for, so one slow recipient must not hold everyone else's next alert.
const MAX_RETRY_WAIT = 3600e3;
const londonDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' });
const londonOffset = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', timeZoneName: 'shortOffset' });

export function forecastExpiry(issued) {
  const at = Date.parse(issued);
  if (!Number.isFinite(at)) return NaN;
  const parts = Object.fromEntries(londonDate.formatToParts(at).map(p => [p.type, p.value]));
  const midnight = Date.UTC(+parts.year, +parts.month - 1, +parts.day + 1);
  // At 00:00 UTC the offset still matches the approaching local midnight on both DST changes.
  const offset = londonOffset.formatToParts(midnight).find(p => p.type === 'timeZoneName').value === 'GMT+1' ? 3600e3 : 0;
  return Math.min(at + MAX_FORECAST_AGE, midnight - offset);
}

// Logs name a subscription by part of its key hash. Its endpoint is enough, with the VAPID key, to
// push to that browser, so it is cut out of any message logged: a push service's reason for
// refusing (a VAPID key mismatch, say) is kept, since without it a failure cannot be diagnosed.
export function redact(text, endpoint) {
  let out = String(text ?? '');
  const parts = [endpoint];
  try { const u = new URL(endpoint); parts.push(u.pathname + u.search, ...u.pathname.split('/'), ...u.searchParams.values()); } catch { /* not a URL */ }
  for (const part of parts.filter((x) => typeof x === 'string' && x.length >= 8).sort((a, b) => b.length - a.length)) out = out.split(part).join('<endpoint>');
  return out.replace(/\s+/g, ' ').trim().slice(0, 200);   // after cutting, so a cut cannot leave half an endpoint
}

function retryAfter(value, at) {
  if (!value) return 0;
  const seconds = Number(value);
  const time = Number.isFinite(seconds) ? at + Math.max(0, seconds) * 1000 : Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

export async function runCron(env, { fetch = globalThis.fetch, log = console.log, now = Date.now } = {}) {
  const authorize = vapidSigner(env); // fails on bad config every run, not only when a spot rises
  const configured = Number(env.SENDS_PER_RUN);
  const perRun = Number.isSafeInteger(configured) && configured > 0 ? configured : SENDS_PER_RUN;
  const tally = { sent: 0, removed: 0, failed: 0 };
  const queued = await env.PUSH.get('queue', 'json');
  const t = now();
  // Revalidate even while draining: a queued warning must not outlive a newer lower forecast.
  const res = await fetch(`${env.SITE_URL}data/alerts.json?t=${Math.floor(t / 60000)}`, { signal: AbortSignal.timeout(15000) });
  if (!res.ok) throw new Error(`alerts.json: HTTP ${res.status}`);
  const alerts = await res.json();
  if (typeof alerts?.generated_at !== 'string' || !isObject(alerts.spots)) throw new Error('alerts.json: unexpected shape');
  const issued = Date.parse(alerts.generated_at), expires = forecastExpiry(alerts.generated_at);
  if (!Number.isFinite(issued) || issued > t + 5 * 60e3) throw new Error('alerts.json: invalid or future issue time');
  if (queued?.items?.length) {
    const state = await env.PUSH.get('state', 'json');
    const checkpointAt = Date.parse(queued.state?.generated_at);
    if (issued < checkpointAt || issued < Date.parse(state?.generated_at)) throw new Error('alerts.json: issue time moved backwards');
    if (queued.state && (!state || Date.parse(state.generated_at) < checkpointAt)) {
      await env.PUSH.put('state', JSON.stringify(queued.state));
    }
    // An expired forecast (8 hours old, or past UK midnight, when "today" in its wording has
    // passed) pauses the queue rather than ending it: every alert is checked against the forecast
    // of the run that sends it, so the next fresh forecast decides what still goes out, in its words.
    if (expires <= t) {
      log(`cron: forecast expired; ${queued.items.length} alerts wait for the next one`);
      return { risen: [], ...tally, queued: queued.items.length };
    }
    return drain(env, queued, perRun, authorize, tally, { fetch, log, now, alerts, expires });
  }
  if (expires <= t) {
    log('cron: forecast expired; nothing compared or sent');
    return { risen: [], ...tally, queued: 0 };
  }

  const prev = await env.PUSH.get('state', 'json');
  if (issued < Date.parse(prev?.generated_at)) throw new Error('alerts.json: issue time moved backwards');
  if (prev?.generated_at === alerts.generated_at) {
    log(`cron: alerts.json unchanged (${alerts.generated_at})`);
    return { risen: [], ...tally, queued: 0 };
  }
  const ranks = {};
  for (const [id, spot] of Object.entries(alerts.spots)) ranks[id] = Number.isInteger(spot?.rank) ? spot.rank : -1;

  const alerted = Object.fromEntries(Object.entries(prev?.alerted ?? {}).filter(([, at]) => t - Date.parse(at) < QUIET_MS));
  let risen = [], items = [];
  if (!prev) {
    // A first run has nothing to compare with; alerting now would alert everyone.
    log(`cron: first run, saved ranks for ${Object.keys(ranks).length} spots, sent nothing`);
  } else {
    risen = Object.keys(ranks).filter((id) => ranks[id] >= HIGH && (prev.ranks?.[id] ?? -1) < HIGH && !alerted[id]);
    for (const id of risen) alerted[id] = new Date(t).toISOString();
    if (risen.length) items = await queueFor(env, alerts, new Set(risen), tally, log);
    log(`cron: ${alerts.generated_at}: ${risen.length} spots rose to high, ${items.length} alerts to send`);
  }
  const state = { generated_at: alerts.generated_at, ranks, ...(Object.keys(alerted).length ? { alerted } : {}) };
  // Persist the complete queue BEFORE advancing the comparison state. If this write fails,
  // the previous ranks remain and the next run can discover the rise again. Sending begins
  // next run: putting and then checkpointing the same KV key within a second is rate-limited.
  if (items.length) await env.PUSH.put('queue', JSON.stringify({ items, state }));
  await env.PUSH.put('state', JSON.stringify(state));
  if (!items.length) return { risen, ...tally, queued: 0 };
  return { risen, ...tally, queued: items.length };
}

async function listKeys(kv, prefix) {
  const keys = [];
  let cursor;
  do {
    const page = await kv.list({ prefix, cursor });
    keys.push(...page.keys);
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return keys;
}

// One alert per subscriber of a risen spot: [{key, spots}]. The spots come from each key's
// metadata; a record without them (too many spots to fit) is read.
async function queueFor(env, alerts, risen, tally, log) {
  const items = [];
  for (const { name: key, metadata } of await listKeys(env.PUSH, 'sub:')) {
    let spots = metadata?.s;
    if (!Array.isArray(spots)) {
      const record = await env.PUSH.get(key, 'json').catch(() => null);
      if (record && !isPushService(record.subscription?.endpoint)) {
        await env.PUSH.delete(key); // stored before its host was dropped from the list
        tally.removed++;
        log(`push ${key.slice(4, 16)}: removed, push service not allowed`);
        continue;
      }
      spots = record?.spots ?? [];
    }
    const hits = spots.filter((id) => risen.has(id));
    if (hits.length) items.push({ key, spots: Object.fromEntries(hits.map(id => [id, alerts.spots[id]])) });
  }
  return items;
}

// Sends the first perRun alerts and keeps the rest in 'queue' for the next runs.
async function drain(env, queued, perRun, authorize, tally, { fetch, log, now, alerts, expires }) {
  const batch = [], rest = [], at = now();
  for (const item of queued.items) {
    if ((item.next_attempt ?? 0) <= at && batch.length < perRun) batch.push(item);
    else rest.push(item);
  }
  const kept = await send(env, batch, authorize, tally, { fetch, log, now, alerts, expires });
  rest.push(...kept); // retries do not block recipients who have not had an attempt yet
  if (rest.length) await env.PUSH.put('queue', JSON.stringify({ ...queued, items: rest }));
  else await env.PUSH.delete('queue');
  log(`cron: sent ${tally.sent}, removed ${tally.removed}, failed ${tally.failed}; ${rest.length} still queued`);
  return { risen: [], ...tally, queued: rest.length };
}

// Returns the alerts to keep in the queue: retries, and any the forecast expired under.
async function send(env, batch, authorize, tally, { fetch, log, now, alerts, expires }) {
  const kept = [];
  const fail = (key, why) => { tally.failed++; log(`push ${key.slice(4, 16)}: ${why}`); };
  await eachLimit(batch, CONCURRENCY, async item => {
    const { key, spots } = item;
    const attempt = (item.attempts ?? 0) + 1;
    const retry = (after = 0) => {
      const at = now(), next = Math.max(at + 120000 * 2 ** (attempt - 1), after);
      if (attempt >= MAX_ATTEMPTS) log(`push ${key.slice(4, 16)}: given up after ${attempt} attempts`);
      else if (next - at > MAX_RETRY_WAIT) log(`push ${key.slice(4, 16)}: given up, the push service asked for a wait of over an hour`);
      else kept.push({ ...item, attempts: attempt, next_attempt: next });
    };
    let endpoint;
    try {
      let record;
      try { record = await env.PUSH.get(key, 'json'); }
      catch { fail(key, 'subscription read unavailable'); retry(); return; }
      if (!record) return;
      endpoint = record.subscription?.endpoint;
      const ids = Object.keys(spots ?? {}).filter(id => record.spots?.includes(id) && alerts.spots[id]?.rank >= HIGH);
      if (!ids.length) return;
      if (!isPushService(endpoint)) {
        await env.PUSH.delete(key); tally.removed++; return;
      }
      const ttl = Math.floor((expires - now()) / 1000);
      if (ttl <= 0) { kept.push(item); return; }   // expired during this run: waits like the rest
      const payload = { ...alertPayload(ids, alerts.spots, env.SITE_URL), issued_at: alerts.generated_at, expires_at: new Date(expires).toISOString() };
      // Retry only transient transport/service failures. A malformed subscription or encryption
      // error is permanent and must not keep consuming delivery attempts.
      let res;
      try { res = await sendPush(record.subscription, payload, authorize, { fetch: async (...args) => {
        try { return await fetch(...args); } catch (err) { throw new TransportError(err?.message); }
      }, ttl }); }
      catch (err) { if (err instanceof TransportError) { fail(key, `network failure: ${redact(err.message, endpoint)}`); retry(); return; } throw err; }
      const status = res.status, after = retryAfter(res.headers.get('Retry-After'), now());
      if (status === 404 || status === 410) {
        await res.body?.cancel();
        await env.PUSH.delete(key); tally.removed++;
      } else if (res.ok) {
        await res.body?.cancel();
        tally.sent++;
      } else {
        const why = redact(await res.text().catch(() => ''), endpoint);
        fail(key, `HTTP ${status}${why ? `: ${why}` : ''}`);
        if (status === 408 || status === 429 || status >= 500) retry(after);
      }
    } catch (err) {
      fail(key, redact(err?.message || 'failed', endpoint));
    }
  });
  return kept;
}
class TransportError extends Error {}

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
