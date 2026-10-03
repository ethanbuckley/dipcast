// Swimmers' reviews on a spot's page: would they swim there again, when they swam, what it was like,
// and up to three photos. The service that takes them in is reviews/, a Cloudflare Worker (its README
// has the setup and the reasons). The build fetches the published reviews and their photos and writes
// them into the site, reviews/index.json and reviews/photos/, so reading reviews asks nothing of anyone
// but this site. Writing one sends it to the service, whose address is in that file; it waits there
// until the operator publishes it, and this browser keeps its id and a key to delete it with.
//
// A plain script, like levels.js: in the page its names are globals (each begins rv, review or REVIEW,
// clear of the page's own), and in Node the last lines export the parts that need no page
// (tests/site_reviews.test.cjs).

const REVIEW_KEY = 'dipcast.reviews';   // this browser's own reviews: id, key, what was sent
const REVIEW_MIN = 3;                   // reviews before a spot has a score
const REVIEW_SHOW = 3;                  // reviews listed before "Show all"
const REVIEW_LIMITS = { photos: 3, text: 1500, name: 40, side: 1280, thumb: 240, file: 40 * 1024 * 1024 };
const REVIEW_REASONS = [['not-about-spot', 'It is not about this spot'], ['rude', 'It is rude or hateful'],
  ['person', 'It shows or names someone'], ['spam', 'It is spam or advertising'], ['other', 'Something else']];
// The service's own rule (reviews/src/index.js), so the form says it before the service does.
const REVIEW_WEB = /https?:\/\/|www\.|\b[a-z0-9-]+\.(com|co\.uk|org|net|uk|io|ly)\b/i;
const rvIcon = d => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const REVIEW_ICON = rvIcon('<path d="M4.5 4.5h15a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H11l-4.5 3.5v-3.5h-2a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z"/><path d="M8 9h8M8 12.5h5"/>');
const REVIEW_CLOSE = rvIcon('<path d="M6 6l12 12M18 6 6 18"/>');

const rvEsc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const rvDay = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const rvToday = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// ------------------------------------------------------------------------------ no page needed

// A spot's score from its published reviews: the share that would swim there again, from three.
function reviewScore(list) {
  const n = list.length, yes = list.filter(r => r.again).length;
  return { n, yes, pct: n >= REVIEW_MIN ? Math.round(100 * yes / n) : null };
}
function reviewSentence({ n, yes }) {
  if (!n) return 'No reviews yet.';
  if (n === 1) return `One swimmer has reviewed it so far, and ${yes ? 'would' : 'would not'} swim here again.`;
  if (n === 2) return yes === 2 ? 'Both swimmers who have reviewed it would swim here again.'
    : yes ? 'One of the two swimmers who have reviewed it would swim here again.' : 'Neither of the two swimmers who have reviewed it would swim here again.';
  return `${yes} of ${n} swimmers would swim here again.`;
}
// Under the spot's name: the score, or how many reviews until it has one; nothing without any.
function reviewLine({ n, pct }) {
  return !n ? '' : pct !== null ? `${pct}% would swim here again` : `${n} review${n === 1 ? '' : 's'}`;
}

// w x h shrunk until its long side (or, short = true, its short side) is at most `side`. Never enlarged.
function reviewFit(w, h, side, short = false) {
  const k = Math.min(1, side / (short ? Math.min(w, h) : Math.max(w, h)));
  return [Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k))];
}
// A thumbnail: 240 px on its short side, so that it fills a square; a panorama's long side kept to
// 640 px, the most the service takes.
function reviewThumb(w, h) {
  const t = reviewFit(w, h, REVIEW_LIMITS.thumb, true);
  return Math.max(...t) > 640 ? reviewFit(w, h, 640) : t;
}

// The first thing wrong with a draft, in the words the form shows, or ''.
function reviewProblem(d, today = rvToday()) {
  if (d.again !== 'yes' && d.again !== 'no') return 'Say whether you would swim here again.';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.swam_on || '') || d.swam_on > today || d.swam_on < '2000-01-01') return 'Choose the day you swam: today or before.';
  if ((d.text || '').length > REVIEW_LIMITS.text) return `Keep the review to ${REVIEW_LIMITS.text} characters.`;
  if ((d.name || '').length > REVIEW_LIMITS.name) return `Keep the name to ${REVIEW_LIMITS.name} characters.`;
  if (REVIEW_WEB.test(d.text || '') || REVIEW_WEB.test(d.name || '')) return 'Leave out web addresses.';
  if (d.photos > 0 && !d.consent) return 'Tick the box to say the photos are yours to share.';
  return '';
}

// This browser's own reviews, read back safely: anything that is not one is dropped, so that the page
// cannot stop on a hand edit or a broken write. A deleted one keeps its id (gone) until the site drops it.
function reviewsMine(text) {
  let v; try { v = JSON.parse(text || '[]'); } catch (e) { return []; }
  return Array.isArray(v) ? v.filter(r => r && typeof r === 'object' && typeof r.id === 'string' && /^[0-9a-f]{20}$/.test(r.id)
    && typeof r.spot === 'string' && (r.gone === true || (typeof r.token === 'string' && typeof r.swam_on === 'string'))) : [];
}

// A spot's reviews as the tile lists them: this browser's own still waiting first, newest sent first,
// then the published ones as the build ordered them (newest swim first), this browser's own marked
// and the ones it has deleted left out. `entry` is the spot's list from reviews/index.json.
function reviewRows(entry, mine, spot) {
  const published = Array.isArray(entry) ? entry : [], ids = new Set(published.map(r => r.id));
  const own = mine.filter(r => r.spot === spot), gone = new Set(own.filter(r => r.gone).map(r => r.id));
  const keys = new Set(own.filter(r => !r.gone).map(r => r.id));
  const waiting = own.filter(r => !r.gone && !ids.has(r.id)).sort((a, b) => String(b.sent_at).localeCompare(String(a.sent_at)))
    .map(r => ({ ...r, waiting: true, mine: true }));
  return [...waiting, ...published.filter(r => !gone.has(r.id)).map(r => (keys.has(r.id) ? { ...r, mine: true } : r))];
}

// Only a listed spot has reviews: a point clicked off the list (unlisted) is nowhere a swimmer could
// review, and the service takes only the site's own spot ids.
const reviewable = d => Boolean(d) && !d.unlisted && /^[A-Za-z0-9_-]{1,80}$/.test(String(d.id));

// Forget what the site no longer needs remembered: a deleted review once the site has dropped it too.
function reviewsTidy(mine, index) {
  const live = new Set(Object.values((index && index.spots) || {}).flat().map(r => r.id));
  return mine.filter(r => !r.gone || live.has(r.id));
}

// ------------------------------------------------------------------------------ the page

let REVIEWS = null, reviewsLoading = null, reviewTurn = 0, reviewViewer = null;
// Once a visit. no-cache: asked of the server, as the forecast is (the offline copy keeps the last one).
const reviewsLoad = () => reviewsLoading || (reviewsLoading = fetch('reviews/index.json', { cache: 'no-cache' })
  .then(r => (r.ok ? r.json() : null)).catch(() => null).then(d => (REVIEWS = d && typeof d === 'object' ? d : null)));
const reviewsNow = () => { try { return reviewsMine(localStorage.getItem(REVIEW_KEY)); } catch (e) { return []; } };
const reviewsKeep = list => { try { localStorage.setItem(REVIEW_KEY, JSON.stringify(list.slice(-100))); return true; } catch (e) { return false; } };

// Called by the page each time it draws a spot (index.html, render): the tile after the others, and
// the score under the spot's name. Nothing while the file loads, nothing if reviews are off, and
// nothing if another spot has been opened meanwhile.
async function mountReviews(d) {
  const turn = ++reviewTurn;
  if (!reviewable(d)) return;
  const data = await reviewsLoad();
  if (turn !== reviewTurn || !data || !data.on) return;
  const after = document.querySelector('#result .stack > .tiles');
  if (!after || document.getElementById('reviews')) return;
  const mine = reviewsNow(), tidy = reviewsTidy(mine, data);
  if (tidy.length < mine.length) reviewsKeep(tidy);
  const sec = document.createElement('section');
  sec.className = 'tile rv-tile'; sec.id = 'reviews'; sec.tabIndex = -1; sec.setAttribute('aria-labelledby', 'rv-h');
  after.after(sec);
  drawReviews(sec, d, data);
}

function reviewItem(r, extra) {
  const who = r.mine ? 'Your review' : rvEsc(r.name || 'A swimmer');
  const n = Array.isArray(r.photos) ? r.photos.length : Number(r.photos) || 0;
  const meta = [who, `swam ${rvDay(r.swam_on)}`, r.waiting ? `waiting to be checked${n ? `, with ${n} photo${n === 1 ? '' : 's'}` : ''}` : ''].filter(Boolean).join(' · ');
  const of = r.mine ? 'your review' : `${rvEsc(r.name || 'a swimmer')}'s review`;
  const pics = r.waiting || !n ? '' : `<ul class="rv-pics">${r.photos.map((p, k) => `<li><button type="button" class="rv-pic" data-photo="${rvEsc(r.id)}-${Number(p.n)}"`
    + ` data-w="${Number(p.w)}" data-h="${Number(p.h)}" aria-label="Photo ${k + 1} of ${n} from ${of}"><img src="reviews/photos/${rvEsc(r.id)}-${Number(p.n)}-t.jpg"`
    + ` width="${Number(p.tw)}" height="${Number(p.th)}" alt="" loading="lazy" decoding="async"></button></li>`).join('')}</ul>`;
  return `<li class="rv"${extra ? ' hidden' : ''} data-id="${rvEsc(r.id)}"><p class="rv-v">${r.again ? 'Would swim here again' : 'Would not swim here again'}</p>`
    + `<p class="rv-meta">${meta}</p>${r.text ? `<p class="rv-text">${rvEsc(r.text)}</p>` : ''}${pics}<div class="rv-acts">`
    + (r.mine ? `<button type="button" class="linkbtn small" data-delete="${rvEsc(r.id)}">Delete</button>` : `<button type="button" class="linkbtn small" data-report="${rvEsc(r.id)}">Report</button>`)
    + '</div></li>';
}

// The tile: the label, the score as its figure with the share drawn, one sentence, the reviews, and
// the way to write one. `say` is a line to show where the button was (after sending or deleting).
function drawReviews(sec, d, data, say = '') {
  const rows = reviewRows((data.spots || {})[d.id], reviewsNow(), d.id), score = reviewScore(rows.filter(r => !r.waiting));
  let h = `<h2 class="t-lab" id="rv-h">${REVIEW_ICON}<span>Swimmers' reviews</span></h2>`;
  if (score.pct !== null) h += `<p class="t-fig">${score.pct}%<small>would swim here again</small></p>`
    + `<div class="rv-bar" role="img" aria-label="${score.yes} of ${score.n} would swim here again"><i style="width:${score.pct}%"></i></div>`;
  else if (score.n) h += `<p class="t-fig">${score.n}<small>${score.n === 1 ? 'review' : 'reviews'}</small></p>`;
  h += `<p class="t-say">${reviewSentence(score)}${data.complete === false ? ' The reviews could not be updated this time, so the newest may be missing.' : ''}</p>`;
  if (rows.length) h += `<ol class="rv-list">${rows.map((r, i) => reviewItem(r, i >= REVIEW_SHOW)).join('')}</ol>`
    + (rows.length > REVIEW_SHOW ? `<button type="button" class="btn quiet" id="rv-all" aria-expanded="false">Show all ${rows.length}</button>` : '');
  h += `<div class="rv-write" id="rv-write">${say ? `<p class="rv-said" id="rv-said" tabindex="-1">${say}</p>` : ''}`
    + (data.submit ? '<button type="button" class="btn primary" id="rv-open">Write a review</button>' : '')
    + '<p class="t-key">Reviews are checked before they appear.</p></div>'
    + '<details class="t-more"><summary><span class="sr">Swimmers\' reviews: what this means</span></summary><p>Each swimmer says whether they would swim here again, '
    + 'and can say what it was like and add photos. The figure is the share who would, once three have reviewed. A review is one swimmer\'s day: it is not a water '
    + 'test, it does not change the forecast, and the water can be different when you go. Reviews are checked before they appear.</p></details>';
  sec.innerHTML = h;
  reviewKind(score);
  sec.onclick = e => reviewClick(e, sec, d, data);
}

// The score under the spot's name, as a link to the tile. Redrawn with the tile.
function reviewKind(score) {
  const kind = document.querySelector('#result .answer .kind'); if (!kind) return;
  kind.querySelectorAll('.rv-kind').forEach(x => x.remove());
  const line = reviewLine(score); if (!line) return;
  kind.insertAdjacentHTML('beforeend', `<span class="rv-kind">${kind.textContent.trim() ? ' · ' : ''}<a href="#reviews">${line}</a></span>`);
}

function reviewClick(e, sec, d, data) {
  const t = e.target.closest('button'); if (!t || !sec.contains(t)) return;
  if (t.id === 'rv-all') { sec.querySelectorAll('.rv[hidden]').forEach(x => { x.hidden = false; }); t.remove(); return; }
  if (t.id === 'rv-open') return reviewOpenForm(sec, d, data);
  if (t.classList.contains('rv-pic')) return reviewShow(t);
  if (t.dataset.report) return reviewReportForm(t, data);
  if (t.dataset.delete) {
    const p = t.parentElement;
    p.innerHTML = `<span class="rv-sure">Delete your review for good?</span> <button type="button" class="linkbtn small" data-sure="${rvEsc(t.dataset.delete)}">Delete it</button> <button type="button" class="linkbtn small" data-keep="1">Keep it</button>`;
    p.querySelector('[data-sure]').focus();
    return;
  }
  if (t.dataset.keep) return drawReviews(sec, d, data);
  if (t.dataset.sure) return reviewDelete(t, sec, d, data);
}

async function reviewDelete(t, sec, d, data) {
  const mine = reviewsNow(), r = mine.find(x => x.id === t.dataset.sure); if (!r) return;
  t.disabled = true;
  const say = (msg) => { const p = t.closest('.rv-acts'); if (p) p.textContent = msg; };
  try {
    const res = await fetch(data.submit + 'reviews/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: r.id, token: r.token }) });
    if (!res.ok) return say(res.status === 403 ? 'The review service did not accept this browser\'s key. Email hello@swimsignal.co.uk to have it deleted.' : 'It could not be deleted just now. Try again later.');
  } catch (err) { return say('It could not be deleted: check your connection and try again.'); }
  reviewsKeep(mine.map(x => (x.id === r.id ? { id: r.id, spot: r.spot, gone: true } : x)));
  drawReviews(sec, d, data, 'Your review is deleted. It leaves the site at its next update, within a few hours.');
  document.getElementById('rv-said')?.focus();
}

function reviewReportForm(t, data) {
  const p = t.parentElement, id = t.dataset.report, sel = `rv-why-${id}`;
  p.innerHTML = `<form class="rv-report"><label class="rv-q" for="${sel}">What is wrong with it?</label><select id="${sel}">`
    + REVIEW_REASONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('') + '</select>'
    + '<span class="rv-send"><button type="submit" class="btn">Send report</button><button type="button" class="btn" data-keep="1">Cancel</button></span></form>';
  const f = p.querySelector('form'); f.querySelector('select').focus();
  f.onsubmit = async e => {
    e.preventDefault(); f.querySelector('[type=submit]').disabled = true;
    let ok = false;
    try { ok = (await fetch(data.submit + 'reviews/report', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, reason: f.querySelector('select').value }) })).ok; } catch (err) { ok = false; }
    p.textContent = ok ? 'Thanks. The review will be checked again.' : 'The report could not be sent. Email hello@swimsignal.co.uk instead.';
  };
}

// A photo at full size, over the page. One dialog, made the first time; Escape, the close button or a
// tap beside the photo closes it.
function reviewShow(b) {
  const src = `reviews/photos/${b.dataset.photo}.jpg`;
  if (!window.HTMLDialogElement) { window.open(src, '_blank', 'noopener'); return; }
  if (!reviewViewer) {
    reviewViewer = document.createElement('dialog'); reviewViewer.className = 'rv-view';
    reviewViewer.innerHTML = `<button type="button" class="rv-close" aria-label="Close the photo">${REVIEW_CLOSE}</button><figure><img alt=""><figcaption></figcaption></figure>`;
    reviewViewer.addEventListener('click', e => { if (e.target === reviewViewer || e.target.closest('.rv-close')) reviewViewer.close(); });
    reviewViewer.addEventListener('close', () => { reviewViewer.querySelector('img').removeAttribute('src'); });
    document.body.append(reviewViewer);
  }
  const img = reviewViewer.querySelector('img'), li = b.closest('.rv');
  img.width = Number(b.dataset.w) || 0; img.height = Number(b.dataset.h) || 0; img.src = src; img.alt = b.getAttribute('aria-label');
  reviewViewer.querySelector('figcaption').textContent = li ? li.querySelector('.rv-meta').textContent : '';
  reviewViewer.showModal();
}

// ------------------------------------------------------------------------------ writing one

function reviewFormHtml() {
  const today = rvToday(), L = REVIEW_LIMITS;
  // data-keep-page: the page does not reload itself to fetch a newer forecast while this is open (index.html).
  return `<form class="rv-form" id="rv-form" novalidate data-keep-page>`
    + '<fieldset><legend class="rv-q">Would you swim here again?</legend><div class="rv-choice">'
    + '<label><input type="radio" name="again" value="yes"><span>Yes</span></label><label><input type="radio" name="again" value="no"><span>No</span></label></div></fieldset>'
    + `<label class="rv-q" for="rv-swam">When did you swim here?</label><input type="date" id="rv-swam" name="swam_on" value="${today}" min="2000-01-01" max="${today}">`
    + `<label class="rv-q" for="rv-text">What was it like? <span class="rv-opt">Optional</span></label>`
    + `<textarea id="rv-text" name="text" rows="4" maxlength="${L.text}" placeholder="Getting in, the water, how busy it was"></textarea>`
    + `<p class="rv-q" id="rv-ph">Photos <span class="rv-opt">Up to ${L.photos}, optional</span></p><ul class="rv-previews" id="rv-previews" aria-labelledby="rv-ph"></ul>`
    + '<label class="btn rv-add" id="rv-add"><input type="file" class="rv-file" id="rv-file" accept="image/*" multiple>Add photos</label>'
    + '<label class="rv-consent" id="rv-consent" hidden><input type="checkbox" name="consent" value="yes"><span>I took these photos, and anyone who can be recognised in them is happy for them to be online.</span></label>'
    + `<label class="rv-q" for="rv-name">Name to show <span class="rv-opt">Optional</span></label><input type="text" id="rv-name" name="name" maxlength="${L.name}" autocomplete="nickname" placeholder="A swimmer">`
    + '<div class="rv-hp" aria-hidden="true"><label>Leave this empty <input type="text" name="website" tabindex="-1" autocomplete="off"></label></div>'
    + '<p class="rv-hint">Say what it was like to swim here, and leave out other people\'s names. Each review is checked before it appears: '
    + '<a href="terms.html#reviews">the review rules</a> · <a href="privacy.html#reviews">what happens to it</a>.</p>'
    + '<div class="rv-send"><button type="submit" class="btn primary">Send review</button><button type="button" class="btn" id="rv-cancel">Cancel</button></div>'
    + '<p class="rv-msg" id="rv-msg" role="status"></p></form>';
}

function reviewOpenForm(sec, d, data) {
  const box = sec.querySelector('#rv-write'); box.innerHTML = reviewFormHtml();
  const f = box.querySelector('form'), msg = f.querySelector('#rv-msg'), file = f.querySelector('#rv-file'), list = f.querySelector('#rv-previews');
  const send = f.querySelector('[type=submit]'), photos = [];
  let busy = 0;
  const say = (text, bad = false) => { msg.textContent = text; msg.classList.toggle('bad', bad); };
  const drawPreviews = () => {
    list.innerHTML = photos.map((p, i) => `<li><img src="${p.url}" alt="Photo ${i + 1}"><button type="button" class="linkbtn small" data-drop="${i}">Remove</button></li>`).join('');
    f.querySelector('#rv-consent').hidden = !photos.length;
    f.querySelector('#rv-add').hidden = photos.length >= REVIEW_LIMITS.photos;
  };
  list.addEventListener('click', e => { const b = e.target.closest('[data-drop]'); if (!b) return;
    const [p] = photos.splice(Number(b.dataset.drop), 1); URL.revokeObjectURL(p.url); drawPreviews(); f.querySelector('#rv-file').focus(); });
  file.addEventListener('change', async () => {
    const files = [...file.files]; file.value = '';
    busy++; send.disabled = true; say('Getting the photos ready…');
    let note = '';
    for (const x of files) {
      if (photos.length >= REVIEW_LIMITS.photos) { note = `Up to ${REVIEW_LIMITS.photos} photos.`; break; }
      if (x.size > REVIEW_LIMITS.file) { note = 'A photo was too large to use.'; continue; }
      try { photos.push(await reviewShrink(x)); drawPreviews(); } catch (err) { note = 'A photo could not be read here. A JPEG or a PNG works anywhere.'; }
    }
    busy--; send.disabled = busy > 0; say(note, Boolean(note));
  });
  f.querySelector('#rv-cancel').addEventListener('click', () => { photos.forEach(p => URL.revokeObjectURL(p.url)); drawReviews(sec, d, data); sec.querySelector('#rv-open')?.focus(); });
  f.addEventListener('submit', async e => {
    e.preventDefault(); if (busy) return;
    const field = sel => f.querySelector(sel);
    const draft = { again: (field('input[name=again]:checked') || {}).value, swam_on: field('#rv-swam').value, text: field('#rv-text').value.trim(),
      name: field('#rv-name').value.trim(), photos: photos.length, consent: field('input[name=consent]').checked };
    const problem = reviewProblem(draft);
    if (problem) { say(problem, true); return; }
    send.disabled = true; say('Sending…');
    const body = new FormData();
    for (const k of ['again', 'swam_on', 'text', 'name']) body.append(k, draft[k]);
    body.append('spot', d.id); body.append('website', field('input[name=website]').value);
    if (photos.length) body.append('consent', 'yes');
    photos.forEach((p, n) => { body.append(`photo${n}`, p.full, `photo${n}.jpg`); body.append(`thumb${n}`, p.thumb, `thumb${n}.jpg`); });
    let res;
    try { res = await fetch(data.submit + 'reviews', { method: 'POST', body }); } catch (err) { res = null; }
    if (!res || !res.ok) {
      const why = !res ? 'Your review could not be sent. Check your connection and try again.'
        : res.status === 429 ? 'Too many reviews from this connection today. Try again tomorrow.'
        : res.status === 413 ? 'The photos are too large together. Try fewer.'
        : res.status === 400 ? (t => `${t.charAt(0).toUpperCase()}${t.slice(1)}.`)((await res.text()).trim())
        : 'The review service did not answer. Try again later.';
      send.disabled = false; say(why, true); return;
    }
    const { id, token } = await res.json();
    const kept = reviewsKeep([...reviewsNow(), { id, token, spot: d.id, again: draft.again === 'yes', swam_on: draft.swam_on, text: draft.text,
      name: draft.name, photos: photos.length, sent_at: new Date().toISOString() }]);
    photos.forEach(p => URL.revokeObjectURL(p.url));
    drawReviews(sec, d, data, kept ? 'Thanks. Your review appears here once it has been checked, usually within a day or two.'
      : 'Thanks. Your review appears here once it has been checked. This browser is not keeping site data, so it cannot show you the review while it waits.');
    document.getElementById('rv-said')?.focus();
  });
  f.querySelector('input[name=again]').focus();
}

// A photo made ready on the device: decoded the right way up, shrunk to 1280 px on its long side and
// a 240 px thumbnail, and saved again as JPEG from a canvas, which leaves out the camera's location
// and every other detail it recorded. The service strips any that a different client sends.
async function reviewShrink(file) {
  const src = await reviewDecode(file);
  try {
    const [w, h] = rvSize(src);
    const full = await reviewJpeg(src, ...reviewFit(w, h, REVIEW_LIMITS.side), 0.82);
    const thumb = await reviewJpeg(src, ...reviewThumb(w, h), 0.78);
    return { full, thumb, url: URL.createObjectURL(thumb) };
  } finally { if (src.close) src.close(); }
}
const rvSize = src => [src.naturalWidth || src.width, src.naturalHeight || src.height];   // an <img>, or an ImageBitmap
async function reviewDecode(file) {
  if (window.createImageBitmap) { try { return await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (e) { /* the <img> below */ } }
  const url = URL.createObjectURL(file), img = new Image();
  try { img.src = url; await img.decode(); return img; } finally { setTimeout(() => URL.revokeObjectURL(url), 0); }
}
// Halving until within twice the size, then the last step: a single big step looks jagged in Safari.
function reviewJpeg(src, w, h, quality) {
  let cur = src, [cw, ch] = rvSize(src);
  while (cw / 2 >= w && ch / 2 >= h) {
    const c = document.createElement('canvas'); c.width = Math.round(cw / 2); c.height = Math.round(ch / 2);
    c.getContext('2d').drawImage(cur, 0, 0, c.width, c.height); cur = c; cw = c.width; ch = c.height;
  }
  const out = document.createElement('canvas'); out.width = w; out.height = h;
  const g = out.getContext('2d'); g.imageSmoothingQuality = 'high'; g.drawImage(cur, 0, 0, w, h);
  return new Promise((ok, no) => out.toBlob(b => (b ? ok(b) : no(new Error('the photo could not be saved'))), 'image/jpeg', quality));
}

if (typeof module === 'object' && module.exports) {
  module.exports = { REVIEW_MIN, REVIEW_LIMITS, reviewScore, reviewSentence, reviewLine, reviewFit, reviewThumb, reviewProblem, reviewsMine, reviewRows, reviewsTidy, reviewItem, reviewable, rvToday };
}
