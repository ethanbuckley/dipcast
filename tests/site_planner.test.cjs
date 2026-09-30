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
test('standing ratings and missing coverage are not called daily forecasts', () => {
  assert.equal(dayHeadline({days:[],classification:{class:'excellent'}},'2026-10-01'),'Rated excellent by the EA');
  assert.equal(dayHeadline({days:[]},'2026-10-01'),'No monitored overflows upstream');
});
