const {test} = require('node:test');
const assert = require('node:assert/strict');
const {dayHeadline, dayLevel, setToday} = require('../src/dipcast/site/levels.js');
setToday('2026-09-30');
const spot = {upstream_summary:{overflows:2}, location:{mode:'river'}, now:{label:'low'}, days:[
  {date:'2026-09-30',label:'low',risk:0,p_ecoli_gt900:0.02},
  {date:'2026-10-01',label:'very high',risk:0.9,p_ecoli_gt900:0.02}
]};
test('the chosen day describes its own risk rather than tomorrow’s worse forecast', () => {
  assert.equal(dayHeadline(spot,'2026-09-30'),'Low risk');
  assert.equal(dayLevel(spot,'2026-09-30'),'low');
  assert.equal(dayHeadline(spot,'2026-10-01'),'Very high risk: sewage spills');
});
test('a missing day never becomes low', () => {
  assert.equal(dayHeadline(spot,'2026-10-04'),'No forecast for this day');
  assert.equal(dayLevel(spot,'2026-10-04'),'no forecast');
});
test('standing poor advice survives a low daily prediction', () => {
  const poor = {...spot,classification:{class:'poor'}};
  assert.equal(dayHeadline(poor,'2026-09-30'),'Rated poor: advice against bathing');
  assert.equal(dayLevel(poor,'2026-09-30'),'high');
});
test('out of season the water-quality estimate is shown but does not set the level', () => {
  const {ecoliBand, ecoliLevel, level, headline} = require('../src/dipcast/site/levels.js');
  const day = {date:'2026-09-30', label:'low', risk:0.05, p_ecoli_gt900:0.56, in_validated_season:false};
  const winter = {upstream_summary:{overflows:2}, location:{mode:'river'}, now:{label:'low'}, days:[day, {...day, date:'2026-10-01'}]};
  assert.equal(ecoliBand(winter, day), 'very high');   // its own row and cell keep the band
  assert.equal(ecoliLevel(winter, day), null);          // but it is not in the level
  assert.equal(dayLevel(winter, '2026-09-30'), 'low');
  assert.equal(dayHeadline(winter, '2026-09-30'), 'Low risk');
  assert.equal(level(winter), 'low');
  assert.equal(headline(winter), 'Low risk for the next five days');
  const summer = {...winter, days: winter.days.map(x => ({...x, in_validated_season:true}))};
  assert.equal(dayLevel(summer, '2026-09-30'), 'very high');
  assert.equal(dayHeadline(summer, '2026-09-30'), 'Very high risk: high E.\u00a0coli likely');
  assert.equal(headline(summer), 'Very high risk today: high E.\u00a0coli likely');
  const unmarked = {...winter, days: winter.days.map(({in_validated_season, ...x}) => x)};   // forecasts built before the flag existed
  assert.equal(dayLevel(unmarked, '2026-09-30'), 'very high');
});
test('the lowest-risk day counts low days among spots with a daily forecast, ties to the earlier day', () => {
  const {bestDay} = require('../src/dipcast/site/levels.js');
  const mk = labels => ({upstream_summary:{overflows:1}, location:{mode:'lake'}, now:{label:'low'},
    days: labels.map((l, i) => ({date: ['2026-09-30','2026-10-01','2026-10-02'][i], label: l, risk: l === 'low' ? 0.01 : 0.5}))});
  const spots = [mk(['low','high','low']), mk(['high','low','low']), {days:[], classification:{class:'excellent'}}];
  assert.deepEqual(bestDay(spots, ['2026-09-30','2026-10-01','2026-10-02']), {date:'2026-10-02', low:2, known:2});
  assert.deepEqual(bestDay(spots, ['2026-09-30','2026-10-01']), {date:'2026-09-30', low:1, known:2});
  assert.equal(bestDay([{days:[]}], ['2026-09-30']), null);
  assert.equal(bestDay(spots, ['2026-10-09']), null);   // no spot has a level that day
});
test('standing ratings and missing coverage are not called daily forecasts', () => {
  assert.equal(dayHeadline({days:[],classification:{class:'sufficient'}},'2026-10-01'),'Rated sufficient by the Environment Agency');
  // An excellent or good rating no longer makes such a spot "low": the plain level says what is true there.
  assert.equal(dayHeadline({days:[],classification:{class:'excellent'}},'2026-10-01'),'No sewage risk from monitored overflows');
  assert.equal(dayHeadline({days:[]},'2026-10-01'),'No sewage risk from monitored overflows');
});

const {evidenceRows, comparisonIds} = require('../src/dipcast/site/experience.js');
test('evidence distinguishes missing feeds, dated records, and out-of-season models', () => {
  const days = spot.days.map(x => ({...x, in_validated_season: x.date < '2026-10-01'}));   // as the build marks them
  const s = {...spot, days, now:{monitored_upstream:1},classification:{class:'poor',year:2025,url:'https://example.org'},algae:{date:'2026-09-10',phrase:'none seen'}};
  const facts = Object.fromEntries(evidenceRows(s,'2026-10-01','Wed 00:08'));
  assert.match(facts['Live spill feeds'], /1 of 2.*Missing reports/);
  assert.match(facts['Environment Agency rating'], /2025.*not today/);
  assert.match(facts['Water samples'], /not included/);
  assert.match(facts['Model limits'], /untested/);
  assert.match(facts['Algae observation'], /10 Sept? 2026.*not a current/);
  // The season is the build's mark on the day, as on the page, not the calendar month.
  assert.doesNotMatch(Object.fromEntries(evidenceRows(s,'2026-09-30',''))['Model limits'], /untested/);
  const unmarked = {...s, days: spot.days};   // no mark on the day (built before it): the calendar month decides
  assert.match(Object.fromEntries(evidenceRows(unmarked,'2026-10-01',''))['Model limits'], /untested/);
  assert.doesNotMatch(Object.fromEntries(evidenceRows(unmarked,'2026-09-30',''))['Model limits'], /untested/);
  assert.match(Object.fromEntries(evidenceRows(s,'2026-10-09',''))['Model limits'], /untested/);   // a day outside the forecast
});
test('a spot without a forecast is described in the headline’s words', () => {
  const lake = {days:[], error:'An isolated lake with no river connection', classification:{class:'excellent', year:2025}};
  assert.equal(Object.fromEntries(evidenceRows(lake,'2026-09-30','')).Forecast, 'No river connection: overflows cannot reach this lake.');
  const away = {days:[], error:'No river or lake within 1.5 km of this point.'};
  assert.equal(Object.fromEntries(evidenceRows(away,'2026-09-30','')).Forecast, 'Not covered by the forecast.');
  const failed = {days:[], error:'forecast failed: timeout'};
  assert.equal(Object.fromEntries(evidenceRows(failed,'2026-09-30','')).Forecast, 'No forecast in this update.');
});
test('missing coverage is not reported as a low risk or current test', () => {
  const facts = Object.fromEntries(evidenceRows({days:[]},'2026-09-30',''));
  assert.match(facts.Forecast,/^No sewage risk from monitored overflows: none is within reach upstream, so there is no daily spill forecast/);
  assert.match(facts['Live spill feeds'], /other pollution sources/);
  assert.match(facts['Algae observation'], /does not mean algae are absent/);
});
test('comparison excludes removed spots and duplicates and never exceeds three', () => {
  assert.deepEqual(comparisonIds(['a','a','old','b','c','d'],['a','b','c','d'].map(id=>({id}))),['a','b','c']);
});
const vm = require('node:vm');
const fs = require('node:fs');
const feedback = fs.readFileSync(require('node:path').join(__dirname,'../src/dipcast/api/static/feedback.html'),'utf8');
const prepareSource = feedback.slice(feedback.indexOf('function prepareEmail('),feedback.indexOf("$('feedback-form').addEventListener"));
const context = vm.createContext({}); vm.runInContext(prepareSource,context);
test('feedback encodes user content without changing the recipient or adding mail headers', () => {
  const r = context.prepareEmail('spot','A&B','', 'Line 1\n&bcc=other@example.org <script>');
  const url = new URL(r.href);
  assert.equal(url.pathname,'hello@swimsignal.co.uk');
  assert.equal(url.searchParams.get('bcc'),null);
  assert.match(url.searchParams.get('body'), /Line 1\n&bcc=/);
  assert.match(r.text,/Spot request/);
});

// ------------------------------------------------------------------------------ headlines in one form
// A level says "risk" wherever it heads a line, and a later day is named in full (docs/DESIGN.md, Words).
const L = require('../src/dipcast/site/levels.js');
const DATES = ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'];
const week = (labels, extra = {}) => ({upstream_summary:{overflows:2}, location:{mode:'river'}, now:{label:'low'}, ...extra,
  days: labels.map((l, i) => ({date: DATES[i], label: l, risk: {low:0.05, moderate:0.2, high:0.5, 'very high':0.8}[l],
    p_ecoli_gt900: 0.02, in_validated_season: false}))});
test('a picked day reads "X risk" at every level, with what set it when raised', () => {
  const s = week(['low', 'moderate', 'high', 'very high', 'low']);
  assert.deepEqual(DATES.slice(0, 4).map(iso => L.dayHeadline(s, iso)),
    ['Low risk', 'Moderate risk: sewage spills', 'High risk: sewage spills', 'Very high risk: sewage spills']);
});
test('a later worse day is named by its full weekday, in the headline and in where the week goes', () => {
  const s = week(['low', 'low', 'low', 'high', 'low']);   // 3 October 2026 is a Saturday
  assert.equal(L.headline(s), 'Low risk now · High risk on Saturday');
  assert.equal(L.weekNext(s), 'High risk on Saturday');
});
test('where the week goes starts at today when the level comes from right now', () => {
  const now = week(['low', 'low', 'low', 'low', 'low'], {now:{label:'moderate'}});
  assert.equal(L.headline(now), 'Moderate risk right now: sewage spills');
  assert.equal(L.weekNext(now), 'Low risk later today');   // was "Low risk tomorrow": the search began a day late
  assert.equal(L.weekNext(week(['high', 'low', 'low', 'low', 'low'])), 'Low risk tomorrow');
  assert.equal(L.weekNext(week(['high', 'high', 'moderate', 'low', 'low'])), 'Low risk by Saturday');
  assert.equal(L.weekNext(week(['high', 'high', 'high', 'high', 'high'])), 'High risk on all five days');
  assert.equal(L.weekNext(week(['low', 'low', 'low', 'low', 'low'], {classification:{class:'poor'}})),
    'At least high risk every day');
});

// ------------------------------------------------------------------------------ the page's own functions
// Top-level definitions taken from index.html's script and run beside levels.js and experience.js, as
// in the browser: a definition runs from its first line to the next line at the margin that does not
// close it.
const pageSrc = (() => { const html = fs.readFileSync(require('node:path').join(__dirname, '../src/dipcast/site/index.html'), 'utf8');
  return html.slice(html.indexOf('<script>\n') + 9, html.lastIndexOf('</script>')).split('\n'); })();
function pageDefs(names) {
  return names.map(n => { const i = pageSrc.findIndex(l => l.startsWith(`const ${n} =`) || l.startsWith(`function ${n}(`));
    assert.ok(i >= 0, `no ${n} in index.html`);
    let j = i + 1; while (j < pageSrc.length && /^[\s})\]+]/.test(pageSrc[j])) j++;
    return pageSrc.slice(i, j).join('\n'); }).join('\n');
}
function pageContext(names, extra = {}) {
  const dir = require('node:path').join(__dirname, '../src/dipcast/site/');
  const ctx = vm.createContext({...extra});
  vm.runInContext(fs.readFileSync(dir + 'experience.js', 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(dir + 'levels.js', 'utf8') + '\nsetToday("2026-09-30");', ctx);
  vm.runInContext(pageDefs(names), ctx);
  return ctx;
}
test('the day-by-day table gives each day the level its row in the five days gives', () => {
  const ctx = pageContext(['esc', 'fmt', 'cls', 'dateLabel', 'glyph', 'ICON', 'dayTable']);
  const poor = week(['low', 'low', 'high', 'low', 'low'], {classification:{class:'poor'}});
  const table = vm.runInContext('d => dayTable(d, 2, false, {})', ctx)(poor);
  const cells = [...table.matchAll(/<span class="badge [\w-]+">([^<]+)<\/span>/g)].map(m => m[1]);
  assert.deepEqual(cells, poor.days.map(x => L.risk(poor, x).level).map(l => l.charAt(0).toUpperCase() + l.slice(1)));
  assert.deepEqual(cells, ['High', 'High', 'High', 'High', 'High']);   // the spills alone said Low on four of them
  assert.doesNotMatch(vm.runInContext('d => dayTable(d, 0, false, {})', ctx)({days: poor.days}), /class="badge/);   // nothing upstream
});
test('stored lists that are not lists, or hold odd entries, read as empty rather than stop the page', () => {
  const {storedList} = require('../src/dipcast/site/experience.js');
  for (const text of [null, '', 'not json', '"abc"', '42', '{"id":"a"}', 'null']) assert.deepEqual(storedList(text), [], String(text));
  assert.deepEqual(storedList('["a", 3, "b"]', x => typeof x === 'string'), ['a', 'b']);
  let stored = '{"id":"a"}';
  const ctx = pageContext(['savedIds', 'SWIM_KEY', 'swims', 'swamOn'], {localStorage: {getItem: () => stored}});
  assert.equal(vm.runInContext('swims().length', ctx), 0);
  assert.equal(vm.runInContext('swamOn("a", "2026-09-30")', ctx), false);   // threw "swims(...).some is not a function"
  stored = '[null, 7, {"id":"a","date":"2026-09-30","level":"low"}]';
  assert.equal(vm.runInContext('swamOn("a", "2026-09-30")', ctx), true);
  assert.equal(vm.runInContext(`savedIds('"abc"').length`, ctx), 0);   // a Set of a string was its letters
  assert.equal(vm.runInContext(`savedIds('["x", {"y":1}]').join()`, ctx), 'x');
});
test('a river gauge reading over a day old is named, but not shown as the level now', () => {
  const ctx = pageContext(['esc', 'fmt', 'glyph', 'ICON', 'rangeBar', 'riverTile']);
  const tileOf = vm.runInContext('riverTile', ctx);
  const gauge = {station: 'Salisbury', river: 'River Avon', distance_km: 2.1, same_river: true, rloi: '1234',
    typical_low_m: 0.2, typical_high_m: 1.4, observed_at: '2026-09-02T09:00:00Z'};
  // Before the build marked old readings (no stale field): as it was.
  const now = tileOf({...gauge, level_m: 0.43, label: 'normal'});
  assert.equal(now.fig, '0.43');
  assert.equal(now.word, 'Usual level');
  assert.match(now.say, /^Not part of the pollution level\. Gauge at Salisbury on the River Avon, 2\.1&nbsp;km away, /);
  assert.equal(tileOf({...gauge, level_m: null}), null);   // no reading at all: no tile
  assert.equal(tileOf(null), null);
  // Stale, as attach_river_levels writes it: the value moved to last_level_m, the level cleared.
  const old = tileOf({...gauge, stale: true, age_hours: 708.4, last_level_m: 0.43, level_m: null, index: null, label: 'unknown'});
  assert.equal(old.fig, '–');
  assert.equal(old.word, undefined);
  assert.equal(old.vis, undefined);   // no range bar: there is no level now to place on it
  assert.match(old.say, /^Last reading 30 days ago, not shown as current\. Not part of the pollution level\. Gauge at Salisbury/);
  assert.match(old.more, /check-for-flooding\.service\.gov\.uk\/station\/1234/);
  assert.match(tileOf({...gauge, stale: true, age_hours: 30, level_m: null}).say, /^Last reading 1 day ago,/);
  assert.match(tileOf({...gauge, stale: true, age_hours: 50, level_m: null}).say, /^Last reading 2 days ago,/);
});
test('a water rated poor names the advice against bathing in its season only, by the day shown', () => {
  const poor = week(['low', 'low', 'low', 'low', 'low'], {classification:{class:'poor'}});
  assert.equal(L.inBathingSeason('2026-05-15'), true);
  assert.equal(L.inBathingSeason('2026-09-30'), true);
  assert.equal(L.inBathingSeason('2026-10-01'), false);
  assert.equal(L.inBathingSeason('2026-05-14'), false);
  // Today is 30 September, the season's last day; tomorrow is out of it. The level is high on both.
  assert.equal(L.headline(poor), 'Rated poor: advice against bathing');
  assert.equal(L.dayHeadline(poor, '2026-09-30'), 'Rated poor: advice against bathing');
  assert.equal(L.dayHeadline(poor, '2026-10-01'), 'Rated poor: advice against bathing from 15 May');
  assert.equal(L.dayLevel(poor, '2026-10-01'), 'high');
  assert.match(L.poorAdvice('2026-09-30'), /^Advice against bathing applies here while the rating is poor/);
  assert.match(L.poorAdvice('2026-10-01'), /^The rating is poor, so the level stays at least high; advice against bathing applies 15 May to 30 September\.$/);
  L.setToday('2026-10-02');
  try { assert.equal(L.headline({...poor, days: poor.days.map((x, i) => ({...x, date: ['2026-10-02','2026-10-03','2026-10-04','2026-10-05','2026-10-06'][i]}))}),
    'Rated poor: advice against bathing from 15 May'); }   // what the alerts say, through headline()
  finally { L.setToday('2026-09-30'); }
});
test('the day table’s † note shows only while a cell carries a †', () => {
  const ctx = pageContext(['esc', 'fmt', 'cls', 'dateLabel', 'glyph', 'ICON', 'dayTable']);
  const off = week(['low', 'low', 'low', 'low', 'low']);   // every day out of season, with a figure
  const river = vm.runInContext('d => dayTable(d, 2, false, {})', ctx)(off);
  assert.match(river, /<sup>†<\/sup>Outside May to September, when the Environment Agency takes no samples/);
  const lake = vm.runInContext('d => dayTable(d, 2, true, {})', ctx)(off);   // the lake's cells read n/a
  assert.doesNotMatch(lake, /†/);
  const blank = vm.runInContext('d => dayTable(d, 2, false, {})', ctx)({days: off.days.map(x => ({...x, p_ecoli_gt900: null}))});
  assert.doesNotMatch(blank, /†/);
});

// ------------------------------------------------------------------------------ the plain levels
// Where the model has nothing to forecast: no monitored overflow within reach upstream, or a lake no
// river flows into. The level says so in words of its own, with one line under it and the rain.
const PAGE = ['esc', 'fmt', 'cls', 'dateLabel', 'glyph', 'ICON', 'tone', 'colour', 'headTone', 'initial', 'VIEW', 'km', 'dist', 'away',
  'PAGE_ID', 'spotPath', 'BOOKMARK', 'untested', 'waterEst', 'week', 'rainMeta', 'rainSaid', 'answerParts', 'savedCard', 'mapLevel',
  'listHeadline', 'listPath', 'spotItem', 'markerTip', 'check', 'spilling', 'summary', 'daysSentence', 'dayTable'];
const plainCtx = () => pageContext(PAGE, {ROOT: {pathname: '/'}});
const text = h => h.replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
// A river with days but no contributors: the build still computes an E. coli figure for it (35% here).
const clearRiver = {id: 'birks', name: 'River Duddon, Birks Bridge', kind: 'river', source: 'curated', upstream_summary: {overflows: 0},
  location: {mode: 'river'}, now: {label: 'unknown', discharging_upstream: 0}, contributors: [], assumptions: {max_upstream_km: 60},
  days: DATES.map((date, i) => ({date, risk: 0, label: 'low', rain_48h_mm: [12.4, 3, 0, 18, 1][i], p_ecoli_gt900: 0.35, in_validated_season: false}))};
const isolatedLake = {id: 'henleaze', name: 'Henleaze Lake', kind: 'lake', source: 'designated', days: [], contributors: [],
  error: 'An isolated lake with no river connection in the network: storm overflows cannot reach it by water, so the forecast has nothing to say about it.',
  classification: {class: 'excellent', year: 2025, url: 'https://example.org/bw'}, assumptions: {max_upstream_km: 60}};

test('a spot with days but nothing upstream reads "No sewage risk from monitored overflows" on every surface', () => {
  const words = 'No sewage risk from monitored overflows';
  assert.equal(L.level(clearRiver), L.NO_OVERFLOWS);
  assert.equal(L.headline(clearRiver), words);
  assert.deepEqual(DATES.map(iso => L.dayHeadline(clearRiver, iso)), DATES.map(() => words));   // the list and map on a picked day
  assert.equal(L.dayLevel(clearRiver, DATES[1]), L.NO_OVERFLOWS);
  assert.equal(L.weekNext(clearRiver), '');
  const ctx = plainCtx(), run = (f, ...a) => vm.runInContext(f, ctx)(...a);
  // The answer: the words in the clear teal, the other risks under them, then the rain.
  const m = run('answerParts', clearRiver);
  assert.deepEqual([m.word, m.tone, m.head, m.sub, text(m.next)],
    [null, 'clear', words, 'Other risks apply: algae, wildlife, runoff and bathers. Check the signs at the water.', '12 mm of rain in the last two days']);
  assert.equal(run('colour', L.NO_OVERFLOWS), '#4aa39a');
  // The saved card, the list row and the map's tooltip say the same.
  const card = run('savedCard', clearRiver);
  assert.match(card, /<p class="big-head clear">No sewage risk from monitored overflows<\/p>/);
  assert.match(text(card), /Other risks apply: algae, wildlife, runoff and bathers\. Check the signs at the water\. 12 mm of rain in the last two days\./);
  assert.match(run('spotItem', clearRiver), /<span class="hl clear">No sewage risk from monitored overflows<\/span>/);
  assert.equal(run('markerTip', clearRiver), 'River Duddon, Birks Bridge: No sewage risk from monitored overflows');
  // No E. coli estimate anywhere: the model was fitted on sites with overflows upstream.
  assert.equal(L.ecoliBand(clearRiver, clearRiver.days[0]), null);
  assert.equal(run('waterEst', clearRiver), '');
  const table = run('dayTable', clearRiver, 0, false, {});
  assert.doesNotMatch(table, /35%/);
  assert.match(table, /E\. coli not shown here<\/b>: estimated only on rivers with monitored overflows upstream/);
  // "Check the signs" once in view: the answer's line has it, so the five days' caveat does not repeat it.
  assert.equal(text(run('check', clearRiver)), 'A forecast, not a water test. This is not a designated bathing water, so the Environment Agency does not test it for bathing.');
  assert.equal(text(run('daysSentence', clearRiver)), 'No storm overflow is monitored within 60 km upstream, so the days show rain instead: rain washes in runoff from farms, roads and wildlife. '
    + 'The wettest day ahead is Saturday, with about 18 mm of rain in the 48 hours to midday.');
  // Today's rain is in the answer already, so the wettest day named is a later one, or none.
  const wetToday = {...clearRiver, days: clearRiver.days.map((x, i) => ({...x, rain_48h_mm: [30, 12, 0, 5, 1][i]}))};
  assert.match(text(run('daysSentence', wetToday)), /The wettest day ahead is tomorrow, with about 12 mm/);
  const dryAfter = {...clearRiver, days: clearRiver.days.map((x, i) => ({...x, rain_48h_mm: [30, 2, 0, 5, 1][i]}))};
  assert.doesNotMatch(text(run('daysSentence', dryAfter)), /wettest/);
});
test('the rain in the last two days is a plain sentence, and absent without a figure', () => {
  const ctx = plainCtx(), said = v => text(vm.runInContext('rainSaid', ctx)({days: [{rain_48h_mm: v}]}));
  assert.equal(said(0), 'No rain in the last two days');
  assert.equal(said(0.3), 'Under 1 mm of rain in the last two days');
  assert.equal(said(1.2), '1 mm of rain in the last two days');
  assert.equal(said(44.7), '45 mm of rain in the last two days');
  assert.equal(said(null), '');
  assert.equal(vm.runInContext('rainSaid', ctx)({days: []}), '');
});
test('an isolated lake reads "No river connection" in the unknown grey, with the same line and no rain', () => {
  const words = 'No river connection: overflows cannot reach this lake';
  assert.equal(L.level(isolatedLake), L.NO_RIVER);   // its excellent rating made it "low" before
  assert.equal(L.headline(isolatedLake), words);
  assert.equal(L.dayHeadline(isolatedLake, DATES[2]), words);
  assert.equal(L.coverage(isolatedLake), words);   // the page's spills row and the comparison
  const ctx = plainCtx(), run = (f, ...a) => vm.runInContext(f, ctx)(...a);
  const m = run('answerParts', isolatedLake);
  assert.deepEqual([m.word, m.tone, m.head, m.sub, m.next],
    [null, 'na', words, 'Other risks apply: algae, wildlife, runoff and bathers. Check the signs at the water.', '']);
  assert.equal(run('colour', L.NO_RIVER), '#98a2aa');
  assert.match(run('savedCard', isolatedLake), /<p class="big-head na">No river connection: overflows cannot reach this lake<\/p>/);
  assert.match(run('spotItem', isolatedLake), /<span class="hl na">No river connection: overflows cannot reach this lake<\/span>/);
  assert.equal(run('markerTip', isolatedLake), `Henleaze Lake: ${words}`);
  assert.match(text(run('check', isolatedLake)), /^A forecast, not a water test\. The EA's page has any advice/);
});
test('a rating or an algae check that raises the level still sets it; excellent or good does not', () => {
  const base = {...clearRiver, source: 'designated', location: {mode: 'lake'}};
  assert.equal(L.level({...base, classification: {class: 'good', year: 2025}}), L.NO_OVERFLOWS);
  const sufficient = {...base, classification: {class: 'sufficient', year: 2025}};
  assert.equal(L.level(sufficient), 'moderate');
  assert.equal(L.headline(sufficient), 'Rated sufficient by the Environment Agency');
  const poor = {...base, classification: {class: 'poor', year: 2025}};
  assert.equal(L.level(poor), 'high');
  assert.equal(L.headline(poor), 'Rated poor: advice against bathing');
  const algae = {...base, classification: {class: 'excellent', year: 2025}, algae: {date: '2026-09-20', level: 3, phrase: 'enough to be objectionable'}};
  assert.equal(L.level(algae), 'high');
  assert.equal(L.headline(algae), 'High risk: algae at the last check');
  assert.equal(L.level({...algae, algae: {...algae.algae, date: '2026-09-01'}}), L.NO_OVERFLOWS);   // over 14 days old
  assert.equal(L.level({...isolatedLake, classification: {class: 'poor', year: 2025}}), 'high');
  // A spot with no water within reach (not an isolated lake) keeps the old rule.
  const away = {days: [], error: 'No river or lake within 1.5 km of this point.'};
  assert.equal(L.level(away), L.NOT_COVERED);
  assert.equal(L.level({...away, classification: {class: 'excellent'}}), 'low');
  assert.equal(L.headline(away), 'Not covered by the forecast');
});
test('the plain levels are not low risk: not in the low count, not offered as lower nearby', () => {
  assert.equal(L.rank(L.level(clearRiver)), -1);
  assert.equal(L.rank(L.level(isolatedLake)), -1);
  assert.equal(L.plainLevel(L.NO_OVERFLOWS) && L.plainLevel(L.NO_RIVER), true);
  assert.equal(L.plainLevel('low') || L.plainLevel(L.NO_FORECAST) || L.plainLevel(L.NOT_COVERED), false);
});
