const {test} = require('node:test');
const assert = require('node:assert/strict');
const {dayHeadline, dayLevel, setToday} = require('../src/dipcast/site/levels.js');
setToday('2026-09-30');
const spot = {upstream_summary:{overflows:2}, location:{mode:'river'}, now:{label:'low'}, days:[
  {date:'2026-09-30',label:'low',risk:0,p_ecoli_gt900:0.02},
  {date:'2026-10-01',label:'very high',risk:0.9,p_ecoli_gt900:0.02}
]};
test('the chosen day describes its own risk rather than tomorrow’s worse forecast', () => {
  assert.equal(dayHeadline(spot,'2026-09-30'),'Low forecast risk');
  assert.equal(dayLevel(spot,'2026-09-30'),'low');
  assert.equal(dayHeadline(spot,'2026-10-01'),'Very high: sewage spills');
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
  assert.equal(dayHeadline(winter, '2026-09-30'), 'Low forecast risk');
  assert.equal(level(winter), 'low');
  assert.equal(headline(winter), 'Low for the next five days');
  const summer = {...winter, days: winter.days.map(x => ({...x, in_validated_season:true}))};
  assert.equal(dayLevel(summer, '2026-09-30'), 'very high');
  assert.equal(dayHeadline(summer, '2026-09-30'), 'Very high: very poor water quality');
  assert.equal(headline(summer), 'Very high today: very poor water quality');
  const unmarked = {...winter, days: winter.days.map(({in_validated_season, ...x}) => x)};   // forecasts built before the flag existed
  assert.equal(dayLevel(unmarked, '2026-09-30'), 'very high');
});
test('standing ratings and missing coverage are not called daily forecasts', () => {
  assert.equal(dayHeadline({days:[],classification:{class:'excellent'}},'2026-10-01'),'Rated excellent by the EA');
  assert.equal(dayHeadline({days:[]},'2026-10-01'),'No monitored overflows upstream');
});

const {evidenceRows, comparisonIds} = require('../src/dipcast/site/experience.js');
test('evidence distinguishes missing feeds, dated records, and out-of-season models', () => {
  const s = {...spot, now:{monitored_upstream:1},classification:{class:'poor',year:2025,url:'https://example.org'},algae:{date:'2026-09-10',phrase:'none seen'}};
  const facts = Object.fromEntries(evidenceRows(s,'2026-10-01','Wed 00:08'));
  assert.match(facts['Live spill feeds'], /1 of 2.*Missing reports/);
  assert.match(facts['EA rating'], /2025.*not today/);
  assert.match(facts['Water samples'], /not included/);
  assert.match(facts['Model limits'], /untested/);
  assert.match(facts['Algae observation'], /10 Sept? 2026.*not a current/);
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
