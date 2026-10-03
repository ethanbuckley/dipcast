// SwimSignal reviews. A spot's page sends a review here: whether the swimmer would swim there again,
// the day they swam, what they wrote, a name to show and up to three photos. Every review waits
// until the operator publishes it on /moderate. The site's build fetches the published ones
// (GET /published) and their photos and serves them with the rest of the site, so a visitor who
// only reads reviews never contacts this Worker. README.md has the setup and the reasons.
//
// D1 (binding DB) holds the reviews, reports and rate-limit counts (migrations/); Workers KV
// (binding PHOTOS) the photos. ADMIN_TOKEN, a secret, opens the moderation queue.

import { BadImage, cleanJpeg } from './jpeg.js';
import { MODERATE_CSS, MODERATE_JS, moderatePage } from './moderate.js';
import { LIMITS, MAX_NAME, MAX_PENDING, MAX_PHOTOS, MAX_TEXT, PHOTOS_PER_DAY, PUBLISHED_LISTED, REASONS } from './rules.js';

const SPOT_ID = /^[A-Za-z0-9_-]{1,80}$/;   // the site's own rule for a spot's id (index.html PAGE_ID)
const REVIEW_ID = /^[0-9a-f]{20}$/;
const PHOTO_PATH = /^\/photos\/([0-9a-f]{20})-([0-2])(-t)?\.jpg$/;
const MAX_FORM = 6 * 1024 * 1024;      // a whole review, photos included
const MAX_JSON = 8 * 1024;
const MAX_PHOTO = 1536 * 1024;          // one photo, which the page shrinks to 1280 px on its long side
const MAX_THUMB = 200 * 1024;           // its thumbnail, 240 px on its short side
const MAX_SIDE = 2048, MAX_THUMB_SIDE = 640;
const WEB_ADDRESS = /https?:\/\/|www\.|\b[a-z0-9-]+\.(com|co\.uk|org|net|uk|io|ly)\b/i;

export default {
  fetch: (request, env) => handleRequest(request, env),
  async scheduled(controller, env) { await forgetOldHits(env); },
};

// ---- replies ----

class Invalid extends Error {}
class Refused extends Error { constructor(status, message) { super(message); this.status = status; } }
const check = (ok, reason) => { if (!ok) throw new Invalid(reason); };
const isObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);
const SAFE = { 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex' };
const reply = (status, text, headers = {}) =>
  new Response(text, { status, headers: { 'Content-Type': 'text/plain; charset=utf-8', ...SAFE, ...headers } });
const json = (status, data, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...SAFE, ...headers } });
const nowISO = () => new Date().toISOString();

// ---- routes ----

// From the site's pages: browsers send Origin on these, and only the site's is accepted. With each,
// the status of a successful answer that carries a body.
const PUBLIC_POSTS = new Map([['/reviews', [submit, 201]], ['/reviews/status', [status, 200]], ['/reviews/delete', [removeOwn]],
  ['/reviews/report', [report]]]);

export async function handleRequest(request, env) {
  const url = new URL(request.url), path = url.pathname;
  try {
    if (PUBLIC_POSTS.has(path)) return await publicPost(request, env, PUBLIC_POSTS.get(path));
    if (request.method !== 'GET' && request.method !== 'POST') return reply(405, 'Method not allowed', { Allow: 'GET, POST' });
    if (request.method === 'GET') {
      if (path === '/published') return json(200, await published(env));
      const photo = PHOTO_PATH.exec(path);
      if (photo) return await servePhoto(request, env, photo[1], Number(photo[2]), Boolean(photo[3]));
      if (path === '/moderate') return moderateShell(env);
      if (path === '/moderate.js') return reply(200, MODERATE_JS, { 'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-cache' });
      if (path === '/moderate.css') return reply(200, MODERATE_CSS, { 'Content-Type': 'text/css; charset=utf-8', 'Cache-Control': 'no-cache' });
      if (path === '/admin/queue') { await requireAdmin(request, env); return json(200, await queue(env)); }
    }
    if (request.method === 'POST' && path === '/admin/decide') {
      await requireAdmin(request, env);
      await decide(await readJson(request), env);
      return new Response(null, { status: 204, headers: SAFE });
    }
    return reply(404, 'Not found');
  } catch (err) {
    if (err instanceof Invalid) return reply(400, err.message);
    if (err instanceof Refused) return reply(err.status, err.message);
    throw err;
  }
}

async function publicPost(request, env, [route, ok]) {
  if (request.method !== 'POST' && request.method !== 'OPTIONS') return reply(405, 'Method not allowed', { Allow: 'POST, OPTIONS' });
  if (!env.ALLOWED_ORIGIN || request.headers.get('Origin') !== env.ALLOWED_ORIGIN) return reply(403, 'Origin not allowed');
  const cors = { 'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN, Vary: 'Origin' };
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: {
      ...cors, ...SAFE,
      'Access-Control-Allow-Methods': 'POST',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    } });
  }
  try {
    const out = await route(request, env);
    return out === undefined ? new Response(null, { status: 204, headers: { ...cors, ...SAFE } }) : json(ok, out, cors);
  } catch (err) {
    if (err instanceof Invalid) return reply(400, err.message, cors);
    if (err instanceof Refused) return reply(err.status, err.message, cors);
    // Anything else (a missing table, a used-up quota) still answers the page with its CORS header,
    // or the browser hides the answer and the page can only guess at the connection.
    console.error('reviews: unexpected error', err && err.stack ? err.stack : err);
    return reply(500, 'The review service had a problem', cors);
  }
}

// ---- helpers ----

const hex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
const randomHex = (n) => hex(crypto.getRandomValues(new Uint8Array(n)));
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
export const sha256 = async (text) => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
async function hmac(key, text) {
  const k = await crypto.subtle.importKey('raw', new TextEncoder().encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', k, new TextEncoder().encode(text)));
}
// Equal strings, in a time that does not depend on where they differ: both are hashed first.
async function same(a, b) {
  const [x, y] = await Promise.all([sha256(String(a)), sha256(String(b))]);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}

// Reads at most MAX_JSON bytes, so an oversized body is refused without buffering it.
async function readJson(request) {
  check(!(Number(request.headers.get('Content-Length')) > MAX_JSON), 'Body too large');
  const chunks = [];
  let size = 0;
  const reader = request.body?.getReader();
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_JSON) { await reader.cancel(); throw new Invalid('Body too large'); }
    chunks.push(value);
  }
  const all = new Uint8Array(size);
  let at = 0;
  for (const c of chunks) { all.set(c, at); at += c.byteLength; }
  try { return JSON.parse(new TextDecoder().decode(all)); } catch { throw new Invalid('Body is not JSON'); }
}

// Text as it will be shown: one form of each letter, no control or direction-changing characters,
// at most two line breaks in a row, no spaces at either end.
export function cleanText(value, max, oneLine = false) {
  check(value === null || value === undefined || typeof value === 'string', 'text must be a string');
  let t = String(value ?? '').normalize('NFC').replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff]/g, (c) => (c === '\t' ? ' ' : ''));
  t = oneLine ? t.replace(/\s+/g, ' ') : t.replace(/[ \u00a0]+\n/g, '\n').replace(/\n{3,}/g, '\n\n');
  t = t.trim();
  check(t.length <= max, `at most ${max} characters`);
  return t;
}

export function checkDay(value, now = Date.now()) {
  check(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value), 'the day you swam must be a date');
  const t = Date.parse(value + 'T12:00:00Z');
  check(!Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === value, 'the day you swam is not a real date');
  check(value >= '2000-01-01', 'the day you swam is too long ago');
  check(t <= now + 36 * 3600 * 1000, 'the day you swam is in the future');   // a day's grace for time zones
  return value;
}

const checkId = (id) => { check(typeof id === 'string' && REVIEW_ID.test(id), 'bad review id'); return id; };

// The connection to count: an IPv4 address whole, an IPv6 one by its first 64 bits, the block one
// home or phone is given, so that one host cannot count as millions of connections.
export function connectionOf(ip) {
  const a = String(ip || '').trim().toLowerCase();
  if (!a.includes(':')) return a || 'unknown';
  if (a.includes('.')) return a.slice(a.lastIndexOf(':') + 1);   // an IPv4 address written as IPv6
  const [head, tail] = a.split('::'), h = head ? head.split(':') : [], t = tail ? tail.split(':') : [];
  const groups = tail === undefined ? h : [...h, ...Array(Math.max(0, 8 - h.length - t.length)).fill('0'), ...t];
  return groups.slice(0, 4).map((g) => g.padStart(4, '0')).join(':') + '::/64';
}

// A connection's requests of one kind today. The key is a keyed hash of the connection, the kind and
// the day, so the table never holds an address, and yesterday's counts cannot be matched to today's.
async function overLimit(request, env, kind) {
  const day = nowISO().slice(0, 10), ip = connectionOf(request.headers.get('CF-Connecting-IP'));
  const key = (await hmac(env.ADMIN_TOKEN || 'no admin token', `${kind}|${day}|${ip}`)).slice(0, 32);
  const row = await env.DB.prepare('INSERT INTO hits (key, day, n) VALUES (?, ?, 1) ON CONFLICT (key, day) DO UPDATE SET n = n + 1 RETURNING n')
    .bind(key, day).first();
  return row.n > LIMITS[kind];
}

export async function forgetOldHits(env, now = Date.now()) {
  const yesterday = new Date(now - 86400 * 1000).toISOString().slice(0, 10);
  await env.DB.prepare('DELETE FROM hits WHERE day < ?').bind(yesterday).run();
}

const photoKeys = (id, n) => [`p:${id}:${n}`, `t:${id}:${n}`];

async function deleteReview(env, id, photos) {
  const count = (() => { try { return JSON.parse(photos).length; } catch { return MAX_PHOTOS; } })();
  await env.DB.batch([
    env.DB.prepare('DELETE FROM reports WHERE review = ?').bind(id),
    env.DB.prepare('DELETE FROM reviews WHERE id = ?').bind(id),
  ]);
  await Promise.all(Array.from({ length: count }, (_, n) => photoKeys(id, n)).flat().map((k) => env.PHOTOS.delete(k)));
}

// ---- a review from the site ----

async function readPhoto(file, maxBytes, maxSide, what) {
  check(file && typeof file === 'object' && typeof file.arrayBuffer === 'function', `${what} is not a file`);
  check(file.size > 0 && file.size <= maxBytes, `${what} is too large`);
  try {
    const { bytes, width, height } = cleanJpeg(await file.arrayBuffer());
    check(width <= maxSide && height <= maxSide, `${what} is too large`);
    return { bytes, width, height };
  } catch (err) {
    if (err instanceof BadImage) throw new Invalid(`${what}: ${err.message}`);
    throw err;
  }
}

async function submit(request, env) {
  const length = Number(request.headers.get('Content-Length'));
  check(length > 0, 'a review must say how long it is (Content-Length)');
  if (length > MAX_FORM) throw new Refused(413, 'The review and its photos are too large');
  check((request.headers.get('Content-Type') || '').startsWith('multipart/form-data'), 'a review is sent as a form');
  if (await overLimit(request, env, 'review')) throw new Refused(429, 'Too many reviews from this connection today');
  let form;
  try { form = await request.formData(); } catch { throw new Invalid('the form could not be read'); }
  const field = (name) => { const v = form.get(name); return typeof v === 'string' ? v : v === null ? '' : undefined; };
  // A field people never see (the page hides it): only a script fills it in. It is told it worked.
  if (field('website')) return { id: randomHex(10), token: b64url(crypto.getRandomValues(new Uint8Array(32))) };

  const spot = field('spot');
  check(typeof spot === 'string' && SPOT_ID.test(spot), 'bad spot id');
  check(['yes', 'no'].includes(field('again')), 'say whether you would swim here again');
  const again = field('again') === 'yes' ? 1 : 0;
  const swamOn = checkDay(field('swam_on'));
  const body = cleanText(field('text'), MAX_TEXT);
  const name = cleanText(field('name'), MAX_NAME, true);
  check(!WEB_ADDRESS.test(body) && !WEB_ADDRESS.test(name), 'leave out web addresses');

  const photos = [];
  for (let n = 0; n < MAX_PHOTOS; n++) {
    const full = form.get(`photo${n}`), thumb = form.get(`thumb${n}`);
    if (full === null && thumb === null) break;
    photos.push({ full: await readPhoto(full, MAX_PHOTO, MAX_SIDE, `photo ${n + 1}`), thumb: await readPhoto(thumb, MAX_THUMB, MAX_THUMB_SIDE, `photo ${n + 1}'s thumbnail`) });
  }
  check(form.get(`photo${photos.length}`) === null && form.get(`thumb${photos.length}`) === null, `at most ${MAX_PHOTOS} photos, numbered from 0`);
  check(!photos.length || field('consent') === 'yes', 'confirm that the photos are yours to share');
  // The caps for everyone together (rules.js): the queue waiting for the operator, and the day's photos.
  const waiting = await env.DB.prepare("SELECT count(*) AS n FROM reviews WHERE status = 'pending'").first();
  if (waiting.n >= MAX_PENDING) throw new Refused(503, 'The queue of reviews to check is full just now. Try again in a few days');
  if (photos.length) {
    const day = nowISO().slice(0, 10);
    const sent = await env.DB.prepare('INSERT INTO hits (key, day, n) VALUES (?, ?, ?) ON CONFLICT (key, day) DO UPDATE SET n = n + excluded.n WHERE n + excluded.n <= ? RETURNING n')
      .bind('photos', day, photos.length, PHOTOS_PER_DAY).first();
    if (!sent) throw new Refused(503, 'No more photos can be taken today. Send the review without them, or try again tomorrow');
  }

  const id = randomHex(10), token = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const sizes = photos.map((p) => ({ w: p.full.width, h: p.full.height, tw: p.thumb.width, th: p.thumb.height }));
  const keys = [];
  try {
    for (const [n, p] of photos.entries()) {
      const [fk, tk] = photoKeys(id, n);
      keys.push(fk, tk);
      await env.PHOTOS.put(fk, p.full.bytes);
      await env.PHOTOS.put(tk, p.thumb.bytes);
    }
    // Check and insert in one SQL statement: simultaneous submissions cannot both take the
    // last queue slot. If it filled while the photos were stored, the catch removes them.
    const stored = await env.DB.prepare("INSERT INTO reviews (id, spot, again, swam_on, body, name, photos, created_at, token_hash) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE (SELECT count(*) FROM reviews WHERE status = 'pending') < ? RETURNING id")
      .bind(id, spot, again, swamOn, body, name, JSON.stringify(sizes), nowISO(), await sha256(token), MAX_PENDING).first();
    if (!stored) throw new Refused(503, 'The queue of reviews to check is full just now. Try again in a few days');
  } catch (err) {
    await Promise.allSettled(keys.map((k) => env.PHOTOS.delete(k)));   // no photos without their review
    throw err;
  }
  return { id, token };
}

// The sender's browser keeps the review's id and key, and can delete it at any time.
async function removeOwn(request, env) {
  const data = await readJson(request);
  check(isObject(data), 'body must be a JSON object');
  const id = checkId(data.id);
  check(typeof data.token === 'string' && data.token.length <= 100, 'bad key');
  if (await overLimit(request, env, 'remove')) throw new Refused(429, 'Too many requests from this connection today');
  const row = await env.DB.prepare('SELECT token_hash, photos FROM reviews WHERE id = ?').bind(id).first();
  if (!row) return undefined;   // already gone: what was asked for is true
  if (!(await same(row.token_hash, await sha256(data.token)))) throw new Refused(403, 'That key does not open this review');
  await deleteReview(env, id, row.photos);
  return undefined;
}

// The sender's browser asks after its own reviews that have not reached the site: still waiting,
// published (the site not rebuilt since), or gone (turned down, or deleted). An id alone is enough to
// ask: a waiting review's id is known only to the browser that sent it, and a published one is public.
async function status(request, env) {
  const data = await readJson(request);
  check(isObject(data) && Array.isArray(data.ids) && data.ids.length >= 1 && data.ids.length <= 20, 'ids must be a list of 1 to 20 review ids');
  const ids = [...new Set(data.ids.map(checkId))];
  if (await overLimit(request, env, 'status')) throw new Refused(429, 'Too many requests from this connection today');
  const { results } = await env.DB.prepare(`SELECT id, status FROM reviews WHERE id IN (${ids.map(() => '?').join(', ')})`).bind(...ids).all();
  const found = new Map(results.map((r) => [r.id, r.status]));
  return Object.fromEntries(ids.map((id) => [id, found.get(id) || 'gone']));
}

async function report(request, env) {
  const data = await readJson(request);
  check(isObject(data), 'body must be a JSON object');
  const id = checkId(data.id);
  check(REASONS.includes(data.reason), 'bad reason');
  if (await overLimit(request, env, 'report')) throw new Refused(429, 'Too many reports from this connection today');
  const row = await env.DB.prepare('SELECT status, (SELECT count(*) FROM reports WHERE review = ?) AS n FROM reviews WHERE id = ?').bind(id, id).first();
  // Nothing to report, or the operator has 50 reports of it to read already.
  if (!row || row.status !== 'published' || row.n >= 50) return undefined;
  await env.DB.prepare('INSERT INTO reports (review, reason, created_at) VALUES (?, ?, ?)').bind(id, data.reason, nowISO()).run();
  return undefined;
}

// ---- for the site's build ----

const fromRow = (r) => ({
  id: r.id, spot: r.spot, again: r.again === 1, swam_on: r.swam_on, text: r.body, name: r.name,
  photos: (() => { try { return JSON.parse(r.photos); } catch { return []; } })(),
  created_at: r.created_at, published_at: r.published_at,
});

// Public: what the site shows anyway. Not when a review was sent, which it does not show.
async function published(env) {
  const { results } = await env.DB.prepare("SELECT * FROM reviews WHERE status = 'published' ORDER BY published_at, id").all();
  return { generated_at: nowISO(), reviews: results.map((r) => { const { created_at, ...out } = fromRow(r); return out; }) };
}

async function servePhoto(request, env, id, n, thumb) {
  const admin = await isAdmin(request, env);
  if (!admin) {
    const row = await env.DB.prepare('SELECT status FROM reviews WHERE id = ?').bind(id).first();
    if (!row || row.status !== 'published') return reply(404, 'Not found');
  }
  const bytes = await env.PHOTOS.get(photoKeys(id, n)[thumb ? 1 : 0], 'arrayBuffer');
  if (!bytes) return reply(404, 'Not found');
  return new Response(bytes, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': admin ? 'private, no-store' : 'public, max-age=3600', ...SAFE } });
}

// ---- moderation ----

async function isAdmin(request, env) {
  const auth = request.headers.get('Authorization') || '';
  return Boolean(env.ADMIN_TOKEN) && auth.startsWith('Bearer ') && (await same(auth.slice(7), env.ADMIN_TOKEN));
}

async function requireAdmin(request, env) {
  if (!env.ADMIN_TOKEN) throw new Refused(503, 'ADMIN_TOKEN is not set: npx wrangler secret put ADMIN_TOKEN');
  if (!(await isAdmin(request, env))) throw new Refused(401, 'Wrong or missing token');
}

async function queue(env) {
  const all = async (sql) => (await env.DB.prepare(sql).all()).results;
  const pending = await all("SELECT * FROM reviews WHERE status = 'pending' ORDER BY created_at LIMIT 200");
  const reported = await all(`SELECT r.*, group_concat(p.reason) AS reasons, max(p.created_at) AS reported_at FROM reviews r
    JOIN reports p ON p.review = r.id GROUP BY r.id ORDER BY reported_at DESC LIMIT 200`);
  const recent = await all(`SELECT * FROM reviews WHERE status = 'published' ORDER BY published_at DESC LIMIT ${PUBLISHED_LISTED}`);
  return {
    pending: pending.map(fromRow),
    reported: reported.map((r) => ({ ...fromRow(r), reasons: String(r.reasons || '').split(',').filter(Boolean), reported_at: r.reported_at })),
    published: recent.map(fromRow),
  };
}

async function decide(data, env) {
  check(isObject(data), 'body must be a JSON object');
  const id = checkId(data.id);
  check(['publish', 'keep', 'delete'].includes(data.action), 'action must be publish, keep or delete');
  const row = await env.DB.prepare('SELECT status, photos FROM reviews WHERE id = ?').bind(id).first();
  if (!row) throw new Refused(404, 'No such review');
  if (data.action === 'delete') return deleteReview(env, id, row.photos);
  if (data.action === 'keep') return env.DB.prepare('DELETE FROM reports WHERE review = ?').bind(id).run();
  await env.DB.prepare("UPDATE reviews SET status = 'published', published_at = ? WHERE id = ? AND status = 'pending'").bind(nowISO(), id).run();
}

function moderateShell(env) {
  const site = new URL(env.SITE_URL || 'https://swimsignal.co.uk/');
  // The page's own script and style, the site's stylesheet and fonts, photos as blobs, and nothing else.
  const csp = [`default-src 'none'`, `script-src 'self'`, `style-src 'self' ${site.origin}`, `font-src ${site.origin}`,
    `img-src 'self' blob: ${site.origin}`, `connect-src 'self' ${site.origin}`, `base-uri 'none'`, `form-action 'none'`, `frame-ancestors 'none'`].join('; ');
  return new Response(moderatePage(site.href), { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': csp, 'Cache-Control': 'no-cache', ...SAFE } });
}
