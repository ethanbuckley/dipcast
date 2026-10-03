// Swimmers' reviews on a spot's page (src/dipcast/site/reviews.js): the parts that need no page.
// node --test tests/site_reviews.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const r = require('../src/dipcast/site/reviews.js');

const id = n => n.toString(16).padStart(20, '0');
const pub = (n, again = true, extra = {}) => ({ id: id(n), again, swam_on: '2026-09-14', text: `Review ${n}`, name: '', photos: [], ...extra });

test('the score is the share who would swim here again, from three reviews', () => {
  assert.deepEqual(r.reviewScore([]), { n: 0, yes: 0, pct: null });
  assert.deepEqual(r.reviewScore([pub(1), pub(2, false)]), { n: 2, yes: 1, pct: null });
  assert.deepEqual(r.reviewScore([pub(1), pub(2), pub(3, false)]), { n: 3, yes: 2, pct: 67 });
  assert.equal(r.reviewLine(r.reviewScore([])), '');
  assert.equal(r.reviewLine(r.reviewScore([pub(1)])), '1 review');
  assert.equal(r.reviewLine(r.reviewScore([pub(1), pub(2)])), '2 reviews');
  assert.equal(r.reviewLine(r.reviewScore([pub(1), pub(2), pub(3, false)])), '67% would swim here again');
});

test('the sentence says what the reviews say, in words', () => {
  const say = list => r.reviewSentence(r.reviewScore(list));
  assert.equal(say([]), 'No reviews yet.');
  assert.equal(say([pub(1)]), 'One swimmer has reviewed it so far, and would swim here again.');
  assert.equal(say([pub(1, false)]), 'One swimmer has reviewed it so far, and would not swim here again.');
  assert.equal(say([pub(1), pub(2)]), 'Both swimmers who have reviewed it would swim here again.');
  assert.equal(say([pub(1), pub(2, false)]), 'One of the two swimmers who have reviewed it would swim here again.');
  assert.equal(say([pub(1, false), pub(2, false)]), 'Neither of the two swimmers who have reviewed it would swim here again.');
  assert.equal(say([pub(1), pub(2), pub(3, false), pub(4)]), '3 of 4 swimmers would swim here again.');
});

test('photos shrink to 1280 px on the long side, thumbnails to 240 on the short side, never larger', () => {
  assert.deepEqual(r.reviewFit(4032, 3024, 1280), [1280, 960]);
  assert.deepEqual(r.reviewFit(3024, 4032, 1280), [960, 1280]);
  assert.deepEqual(r.reviewFit(800, 600, 1280), [800, 600]);
  assert.deepEqual(r.reviewThumb(4032, 3024), [320, 240]);
  assert.deepEqual(r.reviewThumb(200, 150), [200, 150]);
  assert.deepEqual(r.reviewThumb(8000, 1000), [640, 80], 'a panorama keeps under the service\'s 640 px');
});

test('the form says what is wrong before the service does', () => {
  const ok = { again: 'yes', swam_on: '2026-10-02', text: 'Lovely', name: 'Sam', photos: 0, consent: false };
  const today = '2026-10-03';
  assert.equal(r.reviewProblem(ok, today), '');
  assert.match(r.reviewProblem({ ...ok, again: '' }, today), /would swim here again/);
  assert.match(r.reviewProblem({ ...ok, swam_on: '2026-10-04' }, today), /today or before/);
  assert.match(r.reviewProblem({ ...ok, swam_on: '' }, today), /today or before/);
  assert.match(r.reviewProblem({ ...ok, text: 'x'.repeat(1501) }, today), /1500 characters/);
  assert.match(r.reviewProblem({ ...ok, text: 'see www.example.com' }, today), /web addresses/);
  assert.match(r.reviewProblem({ ...ok, name: 'riverswims.co.uk' }, today), /web addresses/);
  assert.match(r.reviewProblem({ ...ok, photos: 2 }, today), /photos are yours/);
  assert.equal(r.reviewProblem({ ...ok, photos: 2, consent: true }, today), '');
});

test('this browser\'s own reviews: read back safely, waiting ones first, deleted ones gone', () => {
  assert.deepEqual(r.reviewsMine('not json'), []);
  assert.deepEqual(r.reviewsMine('{"id":"x"}'), []);
  const mine = r.reviewsMine(JSON.stringify([
    { id: id(1), token: 'k1', spot: 'a', again: true, swam_on: '2026-09-14', text: 'Mine, published', photos: 0, sent_at: '2026-09-14T10:00:00Z' },
    { id: id(9), token: 'k9', spot: 'a', again: false, swam_on: '2026-10-01', text: 'Mine, waiting', photos: 2, sent_at: '2026-10-01T10:00:00Z' },
    { id: id(2), spot: 'a', gone: true },
    { id: id(8), token: 'k8', spot: 'b', again: true, swam_on: '2026-10-01', photos: 0, sent_at: '2026-10-01T09:00:00Z' },
    { id: 'short', token: 'k', spot: 'a', swam_on: '2026-10-01' },
  ]));
  assert.equal(mine.length, 4);
  const rows = r.reviewRows([pub(3), pub(1), pub(2)], mine, 'a');
  assert.deepEqual(rows.map(x => x.id), [id(9), id(3), id(1)]);
  assert.equal(rows[0].waiting, true);
  assert.equal(rows[2].mine, true);
  assert.equal(rows[1].mine, undefined);
  // The score counts the published ones the page shows, not the waiting one or the deleted one.
  assert.deepEqual(r.reviewScore(rows.filter(x => !x.waiting)), { n: 2, yes: 2, pct: null });
  // Once the site has dropped a deleted review, this browser forgets it too.
  assert.deepEqual(r.reviewsTidy(mine, { spots: { a: [pub(2)] } }).map(x => x.id), [id(1), id(9), id(2), id(8)]);
  assert.deepEqual(r.reviewsTidy(mine, { spots: { a: [pub(1)] } }).map(x => x.id), [id(1), id(9), id(8)]);
});

test('a review is listed as text, never markup, with its photos as buttons', () => {
  const html = r.reviewItem({ ...pub(4, false), name: '<img src=x onerror=alert(1)>', text: 'Fine & "dandy"\n<b>no</b>',
    photos: [{ n: 0, w: 1280, h: 960, tw: 320, th: 240 }] }, false);
  assert.ok(!html.includes('<img src=x') && html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('Fine &amp; &quot;dandy&quot;\n&lt;b&gt;no&lt;/b&gt;'));
  assert.ok(html.includes('Would not swim here again') && html.includes('swam 14 Sept 2026'));
  assert.ok(html.includes(`src="reviews/photos/${id(4)}-0-t.jpg"`) && html.includes(`data-photo="${id(4)}-0"`));
  assert.ok(html.includes('data-report=') && !html.includes('data-delete='));
  const mine = r.reviewItem({ ...pub(5), mine: true, waiting: true, photos: 2, token: 'k' }, true);
  assert.ok(mine.includes(' hidden') && mine.includes('Your review') && mine.includes('waiting to be checked, with 2 photos'));
  assert.ok(mine.includes('data-delete=') && !mine.includes('<ul class="rv-pics">'));
});

test('only a listed spot has reviews, never a point clicked off the list', () => {
  assert.equal(r.reviewable({ id: 'wharfe-burnsall', name: 'Burnsall' }), true);
  assert.equal(r.reviewable({ id: 'point-54.12345,-2.12345', unlisted: true }), false);
  assert.equal(r.reviewable({ id: 'Bad Id' }), false);   // an id the site gives no page of its own
  assert.equal(r.reviewable(null), false);
});

test('a review of yours the service has published says it is on its way to the site', () => {
  const html = r.reviewItem({ ...pub(6), mine: true, waiting: true, photos: 0, token: 'k', state: 'published' }, false);
  assert.ok(html.includes('published: on the site at its next update') && !html.includes('waiting to be checked'));
});
