// Plain, testable evidence summaries. No invented measurements or confidence scores.
// The level rules (levels.js): the page loads them after this file, so they are looked up when a
// summary is made; Node requires them.
const rules = () => typeof headParts === 'function' ? { coverage } : require('./levels.js');
const dayMonthYear = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
function evidenceRows(s, iso, issued, generatedAt = null) {
  const total = s.upstream_summary?.overflows || 0, monitored = s.now?.monitored_upstream;
  const day = (s.days || []).find(x => x.date === iso), cl = s.classification;
  // The build's mark on the day, as the page goes by (ecoliUntested); a day without one (no forecast
  // that day, or one built before the mark) goes by the calendar month, May to September.
  const offSeason = day && typeof day.in_validated_season === 'boolean' ? !day.in_validated_season
    : ![5, 6, 7, 8, 9].includes(Number(iso.slice(5, 7)));
  const model = s.error ? `${rules().coverage(s)}.` : !total ? 'No daily spill forecast: no monitored overflows upstream.'
    : !day || day.risk == null ? 'No spill forecast for this day.' : 'Model prediction, not a water sample.';
  return [
    ['Forecast', model + (issued ? ` Issued ${issued}.` : '')],
    ['Live spill feeds', !total ? 'No monitored overflows upstream; other pollution sources may still affect this water.'
      : monitored == null ? `Live reporting coverage unavailable for ${total} upstream overflows.`
      : `${monitored} of ${total} upstream overflows report live in this update.${monitored < total ? ' Missing reports do not mean no spills.' : ''}`],
    ['Environment Agency rating', cl?.class ? `${cl.class.charAt(0).toUpperCase() + cl.class.slice(1)}${cl.year ? ' · ' + cl.year : ''}. Based on up to four seasons of samples; not today’s water quality.`
      : s.source === 'designated' ? 'Designated bathing water; no rating available in this update.' : 'Not an Environment Agency designated bathing water. No bathing-water rating shown.'],
    ['Water samples', 'Individual bacterial sample results are not included here.' + (cl?.url ? ' Check the Environment Agency’s page for dated results and current advice.' : ' No current water test is shown.')],
    ['Model limits', total && !s.error && s.location?.mode !== 'lake'
      ? (offSeason ? 'Outside May–September: the E. coli estimate is untested for this season.' : 'E. coli model tested on river bathing waters in May–September; it is not a test of this spot today.')
      : 'No validated daily E. coli estimate is shown here.'],
    ['Algae observation', s.algae?.date ? `${s.algae.phrase || 'Visual check'} · ${dayMonthYear(s.algae.date)}. A past visual observation, not a current algae warning.` : 'No algae observation in this update. This does not mean algae are absent.'],
    ['Local warnings', [...flowFacts(s, Date.now(), generatedAt).filter(f => f.flood).map(f => flowSentence(f)),
      'Short-term pollution warnings are not fetched by this app. Check official advice and signs at the water.'].join(' ')]
  ];
}
// "Too high to swim" (build_site.attach_flow_state): the river high or rising fast at a gauge on the
// spot's own river, and the Environment Agency's flood alerts and warnings in force within 10 km, the
// most severe first. A different hazard from pollution, so never part of the level. Each is said only
// while it can still be true when read: not from a gauge reading over a day old (flows.MAX_READING_AGE_H),
// a rise whose last reading is over six hours old, or a build over a day old. So a stale reading never
// shows "River high", whether the build found it stale or it went stale on a phone since.
const FLOW_MAX_H = { reading: 24, rise: 6, build: 24 };
const hoursAgo = (iso, now) => { const t = Date.parse(iso); return Number.isNaN(t) ? Infinity : (now - t) / 36e5; };
const clockTime = iso => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' });
function flowFacts(s, now = Date.now(), generatedAt = null) {
  if (generatedAt && hoursAgo(generatedAt, now) > FLOW_MAX_H.build) return [];
  const rs = s.river_state || {}, out = [];
  const current = !rs.stale && !!rs.station && hoursAgo(rs.observed_at, now) <= FLOW_MAX_H.reading;
  if (s.flow_state === 'high' && current) out.push({ lead: 'River high', say: `the gauge at ${rs.station} is above its usual range` });
  else if (s.flow_state === 'rising fast' && current && typeof rs.rise_6h_m === 'number' && hoursAgo(rs.rise_to, now) <= FLOW_MAX_H.rise)
    out.push({ lead: 'River rising fast', say: `the gauge at ${rs.station} rose ${rs.rise_6h_m.toFixed(2)} m between ${clockTime(rs.rise_from)} and ${clockTime(rs.rise_to)}` });
  const fl = (s.flood_alerts || []).filter(f => f && f.severity);
  if (fl.length) out.push({ flood: true, lead: `${fl[0].severity} in force nearby`, area: fl[0].area || '', url: fl[0].url || '', more: fl.length - 1 });
  return out;
}
// One fact as a sentence: plain text for the comparison, or HTML for the answer (escaped here, with
// the lead in bold and the flood area linked to the Environment Agency's page for it).
const htmlText = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]);
function flowSentence(f, html = false) {
  const e = html ? htmlText : x => x, lead = html ? `<b>${e(f.lead)}</b>` : f.lead;
  if (!f.flood) return `${lead}: ${e(f.say)}.`;
  const area = !f.area ? '' : html && f.url ? `<a href="${e(f.url)}">${e(f.area)}</a>` : e(f.area);
  return `${lead} (Environment Agency)${area ? ': ' + area : ''}${f.more ? ` and ${f.more} more` : ''}.`;
}
// A list kept in localStorage (saved spots, the swim log), read back safely: anything but an array
// (a hand edit, another version's format, a broken write) reads as empty, and entries keep() turns
// down are dropped, so that the page cannot stop on them.
function storedList(text, keep = () => true) {
  let v; try { v = JSON.parse(text || '[]'); } catch (e) { return []; }
  return Array.isArray(v) ? v.filter(keep) : [];
}
function comparisonIds(ids, available) {
  const valid = new Set(available.map(s => s.id));
  return [...new Set(ids.filter(id => valid.has(id)))].slice(0,3);
}
if (typeof module === 'object' && module.exports) module.exports = {evidenceRows, comparisonIds, storedList, flowFacts, flowSentence};
