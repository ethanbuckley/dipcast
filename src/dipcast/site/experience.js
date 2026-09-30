// Plain, testable evidence summaries. No invented measurements or confidence scores.
function evidenceRows(s, iso, issued) {
  const total = s.upstream_summary?.overflows || 0, monitored = s.now?.monitored_upstream;
  const day = (s.days || []).find(x => x.date === iso), cl = s.classification;
  const offSeason = ![5,6,7,8,9].includes(Number(iso.slice(5,7)));
  const model = s.error ? 'Forecast unavailable for this spot.' : !total ? 'No daily spill forecast: no monitored overflows upstream.'
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
    ['Algae observation', s.algae?.date ? `${s.algae.phrase || 'Visual check'} · ${s.algae.date}. A past visual observation, not a current algae warning.` : 'No algae observation in this update. This does not mean algae are absent.'],
    ['Local warnings', 'Short-term warnings are not fetched by this app. Check official advice and signs at the water.']
  ];
}
function comparisonIds(ids, available) {
  const valid = new Set(available.map(s => s.id));
  return [...new Set(ids.filter(id => valid.has(id)))].slice(0,3);
}
if (typeof module === 'object' && module.exports) module.exports = {evidenceRows, comparisonIds};
