// The moderation page, /moderate: every review waiting to be published, every reported one, and
// the latest published, each with Publish, Keep or Delete. It is served by the Worker, so the
// queue it reads (/admin/queue) is on its own origin; it borrows the site's stylesheet and fonts
// for its look. The admin token is pasted once and kept in this browser's local storage. Review
// text is only ever set as text (textContent), never as markup, and the Content-Security-Policy
// (index.js, moderateShell) allows no inline script, so a review cannot run code on this page.

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const MARK = '<svg viewBox="0 0 512 512" aria-hidden="true"><rect width="512" height="512" fill="#0f5a61"/>'
  + '<path d="M430-20C330 110 470 230 300 290S110 380 190 540" fill="none" stroke="#5CC2B5" stroke-width="70" stroke-linecap="round"/>'
  + '<circle cx="318" cy="138" r="38" fill="#F08A4B"/><circle cx="165" cy="358" r="46" fill="none" stroke="#fff" stroke-width="22"/></svg>';

export function moderatePage(site) {
  const s = esc(site);
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reviews to check · SwimSignal</title>
<meta name="robots" content="noindex">
<meta name="theme-color" content="#3d5b5d">
<link rel="stylesheet" href="${s}page.css">
<link rel="stylesheet" href="/moderate.css">
</head><body data-site="${s}">
<header class="top"><a class="brand" href="${s}">${MARK}SwimSignal</a><nav aria-label="Site"><a href="/moderate" aria-current="page">Reviews</a></nav></header>
<main class="doc">
<h1>Reviews to check</h1>
<p class="lead">Each review waits here until you publish it. A published review reaches the site at its next build.</p>
<details class="fold"><summary>What to publish</summary>
<ul>
<li>Publish a review that says what it was like to swim at the spot, good or bad.</li>
<li>Delete one about something else, or one that is abusive, advertises, or names or identifies another person.</li>
<li>Delete one that accuses a named person or business of something, a crime or a pollution incident say: that is for the Environment Agency or the police, not a review.</li>
<li>Delete one with a photo in which someone can be recognised, unless it is plainly the sender, and any in which a child can be.</li>
<li>A review is published or deleted whole: there is no editing.</li>
</ul></details>
<form id="signin" class="panel" hidden>
<label for="token">Admin token</label>
<input id="token" type="password" autocomplete="current-password" required>
<p class="hint">The ADMIN_TOKEN secret set with wrangler. This browser keeps it until you choose to forget it.</p>
<div class="actions"><button class="btn primary" type="submit">Open the queue</button></div>
</form>
<p id="msg" role="status"></p>
<div id="queue" hidden>
<h2>Waiting <span class="muted" id="n-pending"></span></h2><ol class="mq-list" id="pending"></ol>
<h2>Reported <span class="muted" id="n-reported"></span></h2><ol class="mq-list" id="reported"></ol>
<h2>Published lately <span class="muted" id="n-published"></span></h2><ol class="mq-list" id="published"></ol>
<div class="actions"><button type="button" class="btn" id="reload">Check again</button><button type="button" class="btn" id="signout">Forget the token on this device</button></div>
</div>
</main>
<script src="/moderate.js"></script>
</body></html>
`;
}

export const MODERATE_CSS = `/* The moderation page, on the site's own page.css (tokens, header, prose, buttons). */
ol.mq-list { list-style: none; padding: 0; margin: 0; }
.mq { margin: 12px 0 0; }
.mq-head { margin: 0; font: 600 var(--fs-head)/1.25 var(--font-display); }
.mq-v { margin: 6px 0 0; font-weight: 700; }
.mq-meta { margin: 2px 0 0; font-size: var(--fs-note); color: var(--muted); }
.mq-text { margin: 10px 0 0; white-space: pre-line; overflow-wrap: anywhere; }
.mq-pics { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 0; }
.mq-pic { padding: 0; border: 0; background: none; border-radius: var(--radius); cursor: zoom-in; }
.mq-pic img { display: block; width: 120px; height: 120px; object-fit: cover; border-radius: var(--radius); }
.mq-full { display: block; max-width: 100%; height: auto; border-radius: var(--radius); }
.mq-reports { margin: 10px 0 0; font-size: var(--fs-note); font-weight: 600; }
#msg:empty { display: none; }
`;

// The page's script. A plain string: it runs in the browser, not in the Worker. No template
// placeholders inside it, so it is the same text whatever the Worker's settings.
export const MODERATE_JS = String.raw`'use strict';
const KEY = 'swimsignal.reviews.admin';
const SITE = document.body.dataset.site;
const $ = (id) => document.getElementById(id);
const REASONS = { 'not-about-spot': 'not about this spot', rude: 'rude or hateful', person: 'shows or names someone', spam: 'spam or advertising', other: 'something else' };
const names = {};
let token = '';
try { token = localStorage.getItem(KEY) || ''; } catch (e) { token = ''; }

const say = (text) => { $('msg').textContent = text; };
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const day = (iso) => new Date(iso.length === 10 ? iso + 'T12:00:00' : iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

function forget(message) {
  token = '';
  try { localStorage.removeItem(KEY); } catch (e) { /* nothing kept */ }
  $('queue').hidden = true; $('signin').hidden = false; say(message || '');
}

async function api(path, init) {
  init = init || {};
  const res = await fetch(path, Object.assign({}, init, { headers: Object.assign({ Authorization: 'Bearer ' + token }, init.headers || {}) }));
  if (res.status === 401) { forget('That token was not accepted. Paste it again.'); throw new Error('not signed in'); }
  if (!res.ok) throw new Error(res.status + ' ' + (await res.text()));
  return res;
}

async function photo(r, n, box) {
  const slot = el('span'); box.append(slot);
  try {
    const thumb = URL.createObjectURL(await (await api('/photos/' + r.id + '-' + n + '-t.jpg')).blob());
    const b = el('button', 'mq-pic'); b.type = 'button'; b.title = 'Show at full size';
    const img = el('img'); img.src = thumb; img.alt = 'Photo ' + (n + 1); b.append(img);
    b.addEventListener('click', async () => {
      const full = el('img', 'mq-full'); full.alt = 'Photo ' + (n + 1) + ', full size';
      full.src = URL.createObjectURL(await (await api('/photos/' + r.id + '-' + n + '.jpg')).blob());
      b.replaceWith(full);
    });
    slot.replaceWith(b);
  } catch (e) { slot.replaceWith(el('span', 'mq-meta', 'Photo ' + (n + 1) + ' could not be loaded.')); }
}

async function act(r, action, li, b) {
  if (action === 'delete' && !confirm('Delete this review and its photos for good?')) return;
  b.disabled = true;
  try {
    await api('/admin/decide', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: r.id, action: action }) });
    li.replaceChildren(el('p', 'mq-meta', action === 'publish' ? 'Published. It reaches the site at its next build.'
      : action === 'keep' ? 'Kept, and its reports cleared.' : 'Deleted, with its photos.'));
  } catch (e) { b.disabled = false; say('Not done: ' + e.message); }
}

function card(r, kind) {
  const li = el('li', 'panel mq');
  const head = el('p', 'mq-head'), a = el('a', null, names[r.spot] || r.spot);
  a.href = SITE + 'spot/' + encodeURIComponent(r.spot) + '/'; a.target = '_blank'; a.rel = 'noopener';
  head.append(a); li.append(head);
  li.append(el('p', 'mq-v', r.again ? 'Would swim here again' : 'Would not swim here again'));
  li.append(el('p', 'mq-meta', (r.name ? 'Shown as ' + r.name : 'No name given') + ' · swam ' + day(r.swam_on) + ' · sent ' + day(r.created_at)
    + (r.published_at ? ' · published ' + day(r.published_at) : '')));
  li.append(r.text ? el('p', 'mq-text', r.text) : el('p', 'mq-meta', 'No words: only the yes or no.'));
  if (r.photos.length) { const box = el('div', 'mq-pics'); li.append(box); r.photos.forEach((_, n) => photo(r, n, box)); }
  if (kind === 'reported') li.append(el('p', 'mq-reports', 'Reported as ' + r.reasons.map((x) => REASONS[x] || x).join(', ')));
  const acts = el('div', 'actions');
  const button = (label, action, primary) => {
    const b = el('button', 'btn' + (primary ? ' primary' : ''), label); b.type = 'button';
    b.addEventListener('click', () => act(r, action, li, b)); acts.append(b);
  };
  if (kind === 'pending') { button('Publish', 'publish', true); button('Delete', 'delete'); }
  else if (kind === 'reported') { button('Keep it', 'keep', true); button('Delete', 'delete'); }
  else button('Delete', 'delete');
  li.append(acts);
  return li;
}

function fill(id, list, kind, empty) {
  $('n-' + id).textContent = '(' + list.length + ')';
  $(id).replaceChildren(...(list.length ? list.map((r) => card(r, kind)) : [el('li', 'mq-meta', empty)]));
}

async function load() {
  $('signin').hidden = true; say('Loading…');
  try {
    const q = await (await api('/admin/queue')).json();
    fill('pending', q.pending, 'pending', 'Nothing waiting.');
    fill('reported', q.reported, 'reported', 'Nothing reported.');
    fill('published', q.published, 'published', 'Nothing published yet.');
    $('queue').hidden = false; say('');
  } catch (e) { if (token) say('The queue could not be loaded: ' + e.message); }
}

$('signin').addEventListener('submit', (e) => {
  e.preventDefault(); token = $('token').value.trim(); if (!token) return;
  try { localStorage.setItem(KEY, token); } catch (e2) { /* kept for this visit only */ }
  $('token').value = ''; load();
});
$('signout').addEventListener('click', () => forget('The token is forgotten on this device.'));
$('reload').addEventListener('click', load);
// The spots' names, from the site's alerts file (small: a name and a level a spot); ids without.
fetch(SITE + 'data/alerts.json').then((r) => r.json()).then((d) => { for (const [id, s] of Object.entries(d.spots || {})) names[id] = s.name; })
  .catch(() => {}).finally(() => { if (token) load(); else forget(''); });
`;
