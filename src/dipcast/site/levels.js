// A spot's pollution level and headline from its forecast: the one copy of the rules, used by the
// page (index.html loads this first), by the build for the alerts file (scripts/alerts.js) and so
// by the alerts themselves, which must never disagree with the page.
//
// A plain script, not a module: in the page its names are globals the page's own script uses,
// and in Node the last lines export them.

// The forecast's own date: the build's date, not the phone's, so "today" is the day the forecast
// was issued for. The page sets it from spots.json; Node calls setToday.
let serverToday = null;
const setToday = iso => { serverToday = iso; };

const ORDER = {low:0, moderate:1, high:2, 'very high':3};
const NOT_COVERED = 'not covered', NO_FORECAST = 'no forecast', NO_OVERFLOWS = 'no overflows';
const localISO = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const addDays = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return localISO(d); };
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const hasData = x => x.risk !== null && x.risk !== undefined;
const today = () => serverToday ?? localISO(new Date());
const shortDay = iso => iso === today() ? 'Today' : new Date(iso + 'T12:00:00').toLocaleDateString('en-GB', {weekday:'short'});
// ------------------------------------------------------------------------------ risk
// A day's level is the worse of two forecasts and one record, because sewage from overflows is
// only part of what makes river water dirty: before this, every Thames spot read "low".
//  - Sewage spills: the exposure index's level, where monitored overflows are upstream.
//  - Water quality: the calibrated E. coli column, the chance a sample would be over 900 per 100 ml,
//    where it was tested (rivers with overflows upstream). Low under 10%: the minimum inland
//    standard ("sufficient", Bathing Water Regulations 2013, schedule 5) is a 90th percentile at
//    or under 900, so a water can be over 900 about one sample in ten and still pass. Moderate to
//    25%, high to 50%, very high above, when a sample would more likely than not be over. Not
//    tighter: on 29 Sep 2026 the model put Pangbourne and Wallingford on the Thames at 11-15%
//    while none of the 20 samples at each this season had been over 900, so a 10% line for
//    "high" would have called clean water poor.
//    In season only (May to September, `in_validated_season`). The EA takes no samples from
//    October to April, so nothing tests the figure then, and on 1 Oct 2026 it alone put 35 of 89
//    spots on high or very high while the spill forecast read low at 78 of 87: a list that red all
//    winter, from an untested number, would tell a swimmer nothing and cost the site its credibility.
//    Out of season the figure is still shown, in its own row and in the day cells, marked untested
//    (†), but it does not set the level or the headline.
//  - The Environment Agency's rating of a designated bathing water: at "poor", advice against
//    bathing applies while the rating stands, so the spot is at least high on every day, whatever
//    the forecast. The local authority that controls the water issues that advice, not the EA
//    (Bathing Water Regulations 2013, reg 13(1)(b)), so the page names no one; the EA issues it
//    for short-term pollution, which this page does not get. 13 of the 38 designated spots were
//    rated poor for 2025; on 29 Sep 2026 all 13 read
//    "low" for today, and 7 of them for today and tomorrow.
//  - The EA sampler's latest look at a bathing water, if under 14 days old: algae "enough to be
//    objectionable" makes the day at least high, "some at intervals" at least moderate. It cannot
//    tell blue-green algae from harmless kinds, so it is a reason to look before going in.
// Where there is no daily forecast (no overflows upstream, or no river connection) the rating is
// the level: excellent or good low, sufficient moderate, poor high. With neither, no level: "low"
// there would read as clean water. Not included: the EA's short-term advice against bathing after
// an incident (Frensham Great Pond's algae warning since 19 Jun 2026, say). Its service refuses
// the build's machines and does not answer other sites' pages, so a spot's page links to it.
const ECOLI_BANDS = [[0.10, 'low'], [0.25, 'moderate'], [0.50, 'high'], [Infinity, 'very high']];
const CLASS_LEVEL = { excellent: 'low', good: 'low', sufficient: 'moderate', poor: 'high' };
const rank = l => ORDER[l] ?? -1;
const higher = (a, b) => rank(b) > rank(a) ? b : a;
const nil = v => v === null || v === undefined;
const overflows = s => (s.upstream_summary || {}).overflows || 0;
const classOf = s => s.classification && s.classification.class ? String(s.classification.class).toLowerCase() : null;
const advisedAgainst = s => classOf(s) === 'poor';
const daily = s => !s.error && overflows(s) > 0;   // a forecast that changes from day to day
const ecoliTested = s => daily(s) && (s.location || {}).mode !== 'lake';
// The figure's band, for its own row and cell, in any month.
const ecoliBand = (s, x) => ecoliTested(s) && x && !nil(x.p_ecoli_gt900) ? ECOLI_BANDS.find(([t]) => x.p_ecoli_gt900 < t)[1] : null;
// Untested from October to April (the build marks each day): shown, but not counted in the level.
const ecoliUntested = x => !!x && x.in_validated_season === false;
const ecoliLevel = (s, x) => ecoliUntested(x) ? null : ecoliBand(s, x);
const algaeAge = a => (Date.parse(today()) - Date.parse(a.date)) / 864e5;
const algaeLevel = s => { const a = s.algae; if (!a || nil(a.level) || algaeAge(a) > 14) return null;
  return a.level >= 3 ? 'high' : a.level >= 2 ? 'moderate' : null; };
// One day, or right now (isNow, from the live overflow status): the level and what set it, 'spill',
// 'water', 'record' or 'algae'. The rating is named unless a forecast is worse; then a tie goes to
// the spills, whose reasons the page can give, then the water, then the algae.
function risk(s, x, isNow = false) {
  const sp = !daily(s) || !x ? null : isNow ? (ORDER[x.label] !== undefined ? x.label : null) : hasData(x) ? x.label : null;
  const wq = isNow ? null : ecoliLevel(s, x), rec = advisedAgainst(s) ? 'high' : null, alg = algaeLevel(s);
  const lv = [sp, wq, rec, alg].reduce(higher, null);
  if (lv === null) return { level: null, by: null };
  return { level: lv, by: rec && rank(rec) >= rank(lv) ? 'record' : lv === sp ? 'spill' : lv === wq ? 'water' : 'algae' };
}
// Right now, today and tomorrow: what the headline and the map colour go by, and the worst of them.
const near = s => [[risk(s, s.now, true), 'right now'], [risk(s, s.days[0]), 'today'], [risk(s, s.days[1]), 'tomorrow']];
const worstNear = s => near(s).reduce((a, b) => rank(b[0].level) > rank(a[0].level) ? b : a);
const worst = s => worstNear(s)[0].level;
const level = s => {
  if (s.error && String(s.error).startsWith('forecast failed')) return NO_FORECAST;
  if (!daily(s)) return higher(CLASS_LEVEL[classOf(s)] ?? null, algaeLevel(s)) || (s.error ? NOT_COVERED : NO_OVERFLOWS);
  return worst(s) ?? NO_FORECAST;
};
// A later day (2 to 4 days ahead) worse than now, today and tomorrow: [its risk, the day].
const laterDay = s => { const w = rank(worst(s));
  const x = s.days.slice(2, 5).map(d => [risk(s, d), d]).filter(([r]) => r.level).reduce((a, b) => !a || rank(b[0].level) > rank(a[0].level) ? b : a, null);
  return x && rank(x[0].level) > w ? x : null; };
const because = r => r.by === 'spill' ? 'sewage spills'
  : r.by === 'water' ? (r.level === 'moderate' ? 'water quality only fair' : r.level === 'very high' ? 'very poor water quality' : 'poor water quality')
  : r.by === 'algae' ? 'algae at the last check' : 'rated poor';
// The gist in a few words, for the list and the top of a spot's page: the worst of now, today and
// tomorrow, when, and what set it; or, if those are all low, the first worse day after them.
// [the level and when, what set it]; the list joins them, a spot's page puts them on two lines.
function headParts(s) {
  const l = level(s);
  if (l === NO_FORECAST) return ['No forecast in this update', ''];
  if (l === NOT_COVERED) return ['Not covered by the forecast', ''];
  if (l === NO_OVERFLOWS) return ['No monitored overflows upstream', ''];
  if (!daily(s) && rank(algaeLevel(s)) > rank(CLASS_LEVEL[classOf(s)] ?? null)) return [cap(l), 'algae at the last check'];
  if (!daily(s) || worstNear(s)[0].by === 'record')
    return advisedAgainst(s) ? ['Rated poor', 'advice against bathing'] : [`Rated ${classOf(s)} by the EA`, ''];
  const [r, when] = worstNear(s);
  if (rank(r.level) > 0) return [`${cap(r.level)} ${when}`, because(r)];
  const x = laterDay(s);
  if (x) return [`Low now · ${cap(x[0].level)} on ${shortDay(x[1].date)}`, ''];
  return [s.days.slice(0, 5).every(hasData) ? 'Low for the next five days' : 'Low on every day with a forecast', ''];
}
const headline = s => headParts(s).filter(Boolean).join(': ');
// The level on one day (a date in s.days), for the map's day picker and the alerts: where the
// forecast does not change from day to day, the spot's own level, the same on every day.
const dayLevel = (s, iso) => {
  if (!daily(s)) return level(s);
  const x = s.days.slice(0, 5).find(d => d.date === iso);
  return x ? risk(s, x).level ?? NO_FORECAST : NO_FORECAST;
};

// Keep standing ratings distinct from daily predictions when planning a particular day.
function dayHeadline(s, iso) {
  if (!daily(s)) return headline(s);
  const x = s.days.slice(0, 5).find(d => d.date === iso), r = x ? risk(s, x) : {level:null};
  if (!r.level) return 'No forecast for this day';
  if (r.by === 'record') return 'Rated poor: advice against bathing';
  return `${cap(r.level)}${rank(r.level) > 0 ? ': ' + because(r) : ' forecast risk'}`;
}

if (typeof module === 'object' && module.exports) {
  module.exports = { ORDER, NOT_COVERED, NO_FORECAST, NO_OVERFLOWS, setToday, today, rank, risk, level, dayLevel,
    headParts, headline, dayHeadline, daily, ecoliBand, ecoliLevel, ecoliUntested };
}
