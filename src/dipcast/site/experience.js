// Plain, testable evidence summaries. No invented measurements or confidence scores.
// The level rules (levels.js): the page loads them after this file, so they are looked up when a
// summary is made; Node requires them.
const rules = () => typeof headParts === 'function' ? { coverage } : require('./levels.js');
const dayMonthYear = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
function evidenceRows(s, iso, issued) {
  const total = s.upstream_summary?.overflows || 0, monitored = s.now?.monitored_upstream;
  const day = (s.days || []).find(x => x.date === iso), cl = s.classification;
  // The page's rule for the E. coli figure (ecoliUntested): the build marks each day in or out of the
  // tested season, rather than going by the calendar month here.
  const offSeason = !!day && day.in_validated_season === false;
  const model = s.error ? `${rules().coverage(s)}.` : !total ? 'No daily spill forecast: no monitored overflows upstream.'
    : !day || day.risk == null ? 'No spill forecast for this day.' : 'Model prediction, not a water sample.';
  return [
    ['Forecast', model + (issued ? ` Issued ${issued}.` : '')],
    ['Live spill feeds', !total ? 'No monitored overflows upstream; other pollution sources may still affect this water.'
      : monitored == null ? `Live reporting coverage unavailable for ${total} upstream overflows.`
      : `${monitored} of ${total} upstream overflows report live in this update.${monitored < total ? ' Missing reports do not mean no spills.' : ''}`],
    ['EA rating', cl?.class ? `${cl.class.charAt(0).toUpperCase() + cl.class.slice(1)}${cl.year ? ' · ' + cl.year : ''}. Based on up to four seasons of samples; not today’s water quality.`
      : s.source === 'designated' ? 'Designated bathing water; no rating available in this update.' : 'Not an EA-designated bathing water. No bathing-water rating shown.'],
    ['Water samples', 'Individual bacterial sample results are not included here.' + (cl?.url ? ' Check the EA page for dated results and current advice.' : ' No current water test is shown.')],
    ['Model limits', total && !s.error && s.location?.mode !== 'lake'
      ? (offSeason ? 'Outside May–September: the E. coli estimate is untested for this season.' : 'E. coli model tested on river bathing waters in May–September; it is not a test of this spot today.')
      : 'No validated daily E. coli estimate is shown here.'],
    ['Algae observation', s.algae?.date ? `${s.algae.phrase || 'Visual check'} · ${dayMonthYear(s.algae.date)}. A past visual observation, not a current algae warning.` : 'No algae observation in this update. This does not mean algae are absent.'],
    ['Local warnings', 'Short-term warnings are not fetched by this app. Check official advice and signs at the water.']
  ];
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
if (typeof module === 'object' && module.exports) module.exports = {evidenceRows, comparisonIds, storedList};
