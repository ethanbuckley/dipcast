// The embed (embed.html?spot=<id>, src/dipcast/site/embed.js): one spot's card for another site's
// page. Its words come from levels.js, so it says what the spot's page and the alerts say.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Embed = require('../src/dipcast/site/embed.js');
const L = require('../src/dipcast/site/levels.js');

const GEN = '2026-10-03T09:00:00+01:00', NOW = Date.parse(GEN) + 2 * 3600e3;   // two hours after the forecast
const DATA = {generated_at: GEN};
const day = (date, label, risk) => ({date, label, risk, p_ecoli_gt900: 0.02, in_validated_season: false});
const river = {id: 'wharfe-burnsall', name: 'River Wharfe, Burnsall', kind: 'river', source: 'curated',
  upstream_summary: {overflows: 5}, location: {mode: 'river'}, now: {label: 'low'},
  days: [day('2026-10-03', 'high', 0.5), day('2026-10-04', 'moderate', 0.2), day('2026-10-05', 'low', 0.01),
         day('2026-10-06', 'low', 0.01), {date: '2026-10-07', label: 'unknown', risk: null}]};

test('the card gives the headline, the five days and where they go, by the page rules', () => {
  const h = Embed.card(river, DATA, NOW);
  L.setToday('2026-10-03');
  assert.match(h, /<h1 class="name"><a href="spot\/wharfe-burnsall\/" target="_blank" rel="noopener">River Wharfe, Burnsall<\/a><\/h1>/);
  assert.ok(h.includes(`<p class="hl high">${L.headline(river)}</p>`) && L.headline(river) === 'High risk today: sewage spills');
  assert.ok(h.includes(`<p class="next">${L.weekNext(river)}</p>`));
  assert.ok(h.includes('Pollution risk, next five days'));   // the bare level words need the heading that names them
  const rows = [...h.matchAll(/<li class="drow (\w+)"><span class="d">([^<]+)<\/span><span class="l">([^<]+)<\/span>.*?width:(\d+)%/g)].map(m => m.slice(1));
  assert.deepEqual(rows, [['high', 'Today', 'High', '75'], ['moderate', 'Sun', 'Moderate', '50'], ['low', 'Mon', 'Low', '25'],
                          ['low', 'Tue', 'Low', '25'], ['na', 'Wed', 'No data', '0']]);
});

test('the caveat, the issue time, the link back and the credits are always on the card', () => {
  const h = Embed.card(river, DATA, NOW);
  assert.ok(h.includes('A forecast, not a water test: check the signs at the water before you swim.'));
  assert.match(h, /Issued [^<]+, 2 h ago · <a class="back" href="spot\/wharfe-burnsall\/" target="_blank" rel="noopener">Full forecast on SwimSignal<\/a>/);
  assert.ok(h.includes('National Storm Overflow Hub (CC BY 4.0)') && h.includes('None of these bodies endorses SwimSignal.'));
  assert.ok(h.includes('<a href="terms.html#data" target="_blank" rel="noopener">All credits and licences</a>'));
  // Every link leaves the frame: one that opened inside it would squeeze the site into someone else's page.
  for (const a of h.match(/<a [^>]*>/g)) assert.match(a, /target="_blank" rel="noopener"/, a);
  assert.ok(!h.includes('Stale'));
});

test('a forecast over eight hours old says so', () => {
  assert.ok(!Embed.card(river, DATA, Date.parse(GEN) + Embed.STALE_MIN * 60e3).includes('Stale'));
  const h = Embed.card(river, DATA, Date.parse(GEN) + 10 * 3600e3);
  assert.ok(h.includes('<b>Stale.</b> This forecast is 10 h old'));
});

test('a bathing water links the Environment Agency page, and a poor one says so on every day', () => {
  const bw = {...river, id: 'bw-x', source: 'designated', classification: {class: 'poor', year: 2025, url: 'https://environment.data.gov.uk/bwq/profiles/x'}};
  const h = Embed.card(bw, DATA, NOW);
  assert.ok(h.includes('<a href="https://environment.data.gov.uk/bwq/profiles/x" target="_blank" rel="noopener">The Environment Agency’s page</a> has any advice against bathing there today'));
  assert.ok(h.includes('Rated poor: advice against bathing from 15 May') && h.includes('At least high risk every day'));
  assert.equal((h.match(/<span class="l">High<\/span>/g) || []).length, 5);   // the rating holds every day at high, even one without rain data
  assert.ok(!Embed.card(river, DATA, NOW).includes('Environment Agency’s page'));   // not a bathing water
});

test('a spot without a daily forecast gets its words and no strip', () => {
  const none = {...river, id: 'semerwater', upstream_summary: {overflows: 0}, days: []};
  const h = Embed.card(none, DATA, NOW);
  assert.ok(h.includes('<p class="hl clear">No monitored overflows upstream</p>') && !h.includes('drow'));
  const isolated = {id: 'tarn', name: 'A Tarn', kind: 'lake', source: 'curated', days: [], error: 'An isolated lake with no river connection.'};
  const t = Embed.card(isolated, DATA, NOW);
  assert.ok(t.includes('<p class="hl na">Not covered by the forecast</p>') && !t.includes('drow') && t.includes('All credits and licences'));
});

test('names are escaped, and an id the site gives no page keeps ?spot=', () => {
  const odd = {...river, id: 'Bad Id', name: '<img src=x onerror=alert(1)>'};
  const h = Embed.card(odd, DATA, NOW);
  assert.ok(!h.includes('<img') && h.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(h.includes('href="./?spot=Bad%20Id"'));
});

test('an unknown or missing id says how to find one', () => {
  const h = Embed.missing('<b>nowhere</b>');
  assert.ok(h.includes('No spot with this id') && h.includes('&lt;b&gt;nowhere&lt;/b&gt;') && !h.includes('<b>nowhere'));
  assert.ok(Embed.missing(null).includes('No spot chosen') && Embed.missing(null).includes('wharfe-burnsall'));
  assert.ok(Embed.failed().includes('could not be loaded'));
});
