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
  assert.equal(dayHeadline({days:[],classification:{class:'excellent'}},'2026-10-01'),'Rated excellent by the Environment Agency');
  assert.equal(dayHeadline({days:[]},'2026-10-01'),'No monitored overflows upstream');
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
  const unmarked = {...s, days: spot.days};   // forecasts built before the mark: tested, as ecoliUntested has it
  assert.doesNotMatch(Object.fromEntries(evidenceRows(unmarked,'2026-10-01',''))['Model limits'], /untested/);
});
test('a spot without a forecast is described in the headline’s words', () => {
  const lake = {days:[], error:'An isolated lake with no river connection', classification:{class:'excellent', year:2025}};
  assert.equal(Object.fromEntries(evidenceRows(lake,'2026-09-30','')).Forecast, 'Not covered by the forecast.');
  const failed = {days:[], error:'forecast failed: timeout'};
  assert.equal(Object.fromEntries(evidenceRows(failed,'2026-09-30','')).Forecast, 'No forecast in this update.');
});
test('missing coverage is not reported as a low risk or current test', () => {
  const facts = Object.fromEntries(evidenceRows({days:[]},'2026-09-30',''));
  assert.match(facts.Forecast,/No daily spill forecast/);
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
  assert.equal(L.weekNext(now), 'Low risk today');   // was "Low risk tomorrow": the search began a day late
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
