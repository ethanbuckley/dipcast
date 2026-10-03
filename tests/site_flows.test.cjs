// "Too high to swim" (roadmap task 8): the sentence under a spot's headline when its river is high
// or rising fast at a gauge on its own river, or a flood alert is in force nearby, from the fields
// build_site.attach_flow_state writes (tests/test_flows.py checks those). Never from a stale reading.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {flowFacts, flowSentence, evidenceRows} = require('../src/dipcast/site/experience.js');

const NOW = Date.parse('2026-10-03T13:30:00Z'), BUILT = '2026-10-03T14:05:00+01:00';
const gauge = (extra = {}) => ({station: 'Addingham', river: 'River Wharfe', level_m: 2.1, typical_low_m: 0.189, typical_high_m: 1.87,
  index: 1.14, label: 'very high', observed_at: '2026-10-03T13:15:00Z', age_hours: 0.3, stale: false, same_river: true, ...extra});
const ALERT = {severity_level: 3, severity: 'Flood alert', area: 'River Wharfe at Ilkley', area_id: '123WAF456',
  url: 'https://check-for-flooding.service.gov.uk/target-area/123WAF456', raised: '2026-10-03T09:20:22'};
const say = (s, now = NOW, built = BUILT) => flowFacts(s, now, built).map(f => flowSentence(f)).join(' ');

test('a river above its usual range says so, naming the gauge', () => {
  assert.equal(say({flow_state: 'high', river_state: gauge()}), 'River high: the gauge at Addingham is above its usual range.');
});

test('no spot shows "River high" from a stale reading', () => {
  // Stale when the build read it (attach_river_levels clears the level and marks it).
  assert.equal(say({flow_state: 'high', river_state: gauge({stale: true, level_m: null, index: null, label: 'unknown'})}), '');
  // Current when built, but over a day old by the time the page is read.
  assert.equal(say({flow_state: 'high', river_state: gauge()}, NOW + 25 * 36e5, null), '');
  // A build over a day old says nothing of the river or the floods.
  assert.equal(say({flow_state: 'high', river_state: gauge(), flood_alerts: [ALERT]}, NOW + 26 * 36e5), '');
  // No time on the reading: not current.
  assert.equal(say({flow_state: 'high', river_state: gauge({observed_at: null})}), '');
});

test('a fast rise gives the measured rise and the times it ran between, and only for six hours', () => {
  const s = {flow_state: 'rising fast', river_state: gauge({level_m: 0.6, index: 0.24, label: 'normal', rise_6h_m: 0.4,
    rise_from: '2026-10-03T07:45:00Z', rise_to: '2026-10-03T13:30:00Z'})};
  assert.equal(say(s), 'River rising fast: the gauge at Addingham rose 0.40 m between 08:45 and 14:30.');   // UK time
  assert.equal(say(s, NOW + 7 * 36e5), '');   // the last reading in the rise is over six hours old
  assert.equal(say({...s, river_state: {...s.river_state, rise_6h_m: undefined}}), '');
});

test('a spot with a live flood alert in the fixture shows the sentence, and the page puts it under the headline', () => {
  const s = {flow_state: null, river_state: null, flood_alerts: [ALERT]};
  assert.equal(say(s), 'Flood alert in force nearby (Environment Agency): River Wharfe at Ilkley.');
  const two = {...s, flood_alerts: [{...ALERT, severity_level: 2, severity: 'Flood warning', area: 'River Wharfe at Burley'}, ALERT]};
  assert.equal(say(two), 'Flood warning in force nearby (Environment Agency): River Wharfe at Burley and 1 more.');
  assert.equal(say({flood_alerts: []}) + say({flood_alerts: null}), '');
  // The page's own line (index.html, flowLine), run on the same fixture.
  const page = fs.readFileSync(path.join(__dirname, '../src/dipcast/site/index.html'), 'utf8');
  const src = page.slice(page.indexOf('const flowLine = '), page.indexOf('\n', page.indexOf('A separate hazard, not part of the pollution level.')));
  const ctx = vm.createContext({flowFacts, flowSentence, DATA: {generated_at: BUILT}, Date: {now: () => NOW}});
  vm.runInContext(src + '\nthis.flowLine = flowLine;', ctx);
  assert.equal(ctx.flowLine(s), '<p class="flow"><b>Flood alert in force nearby</b> (Environment Agency): '
    + '<a href="https://check-for-flooding.service.gov.uk/target-area/123WAF456">River Wharfe at Ilkley</a>. A separate hazard, not part of the pollution level.</p>');
  assert.equal(ctx.flowLine({flow_state: 'high', river_state: gauge(), flood_alerts: [ALERT]}).match(/<b>/g).length, 2);   // both, river first
  assert.equal(ctx.flowLine({flow_state: null, flood_alerts: []}), '');
  assert.ok(page.includes('flowLine(d) + `<p class="issued">'), 'the line sits in the answer, before the issue time');
});

test('names from the feed are escaped in the page and plain in the comparison', () => {
  const s = {flood_alerts: [{...ALERT, area: 'Beck <b>&</b> "Brook"', url: 'https://check-for-flooding.service.gov.uk/target-area/X"onmouseover="y'}]};
  const html = flowFacts(s, NOW, BUILT).map(f => flowSentence(f, true)).join(' ');
  assert.ok(html.includes('Beck &lt;b&gt;&amp;&lt;/b&gt; &quot;Brook&quot;') && !html.includes('"onmouseover'));
  const built = new Date().toISOString();   // the comparison reads the clock
  const rows = Object.fromEntries(evidenceRows({days: [], ...s}, '2026-10-03', '', built));
  assert.match(rows['Local warnings'], /^Flood alert in force nearby \(Environment Agency\): Beck <b>&<\/b> "Brook"\. Short-term pollution warnings are not fetched/);
  assert.equal(Object.fromEntries(evidenceRows({days: []}, '2026-10-03', '', built))['Local warnings'],
    'Short-term pollution warnings are not fetched by this app. Check official advice and signs at the water.');
});
