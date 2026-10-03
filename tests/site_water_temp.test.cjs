// The Water temperature tile (index.html, waterTile), run in Node beside levels.js as in the browser:
// a reading is shown only with where the sensor is, how far along the river, and how old it is now.
// On its own: node --test tests/site_water_temp.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

// Top-level definitions taken from index.html's script (as site_planner.test.cjs takes them): a
// definition runs from its first line to the next line at the margin that does not close it.
const pageSrc = (() => { const html = fs.readFileSync(join(__dirname, '../src/dipcast/site/index.html'), 'utf8');
  return html.slice(html.indexOf('<script>\n') + 9, html.lastIndexOf('</script>')).split('\n'); })();
function pageDefs(names) {
  return names.map(n => { const i = pageSrc.findIndex(l => l.startsWith(`const ${n} =`) || l.startsWith(`function ${n}(`));
    assert.ok(i >= 0, `no ${n} in index.html`);
    let j = i + 1; while (j < pageSrc.length && /^[\s})\]+]/.test(pageSrc[j])) j++;
    return pageSrc.slice(i, j).join('\n'); }).join('\n');
}
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(join(__dirname, '../src/dipcast/site/levels.js'), 'utf8'), ctx);
vm.runInContext(pageDefs(['esc', 'fmt', 'glyph', 'ago', 'waterTile']), ctx);
const waterTile = vm.runInContext('waterTile', ctx);

const hoursAgo = h => new Date(Date.now() - h * 3600e3).toISOString();
// As build_site.attach_water_temperature writes it for Friars Meadow on 3 Oct 2026.
const bures = { temp_c: 16.3, observed_at: hoursAgo(2), age_hours: 1.0, quality: 'Unchecked', station: 'Bures Mill',
  where: 'at Bures Mill', river: 'Stour', station_id: 'E02254A', url: 'https://environment.data.gov.uk/hydrology/station/E02254A',
  distance_km: 8.1, direction: 'downstream', river_km: 10.9 };

test('a reading is shown with where the sensor is, how far along the river and how old it is', () => {
  const t = waterTile(bures);
  assert.equal(t.label, 'Water temperature');
  assert.equal(t.fig, '16°');
  assert.equal(t.unit, 'C');
  assert.equal(t.say, 'Measured at Bures Mill on the Stour, 10.9&nbsp;km downstream, 2 h ago.');
  assert.match(t.more, /not yet checked/);
  assert.match(t.more, /href="https:\/\/environment\.data\.gov\.uk\/hydrology\/station\/E02254A"/);
  assert.match(t.more, /Not part of the pollution level\./);
  assert.match(waterTile({ ...bures, direction: 'upstream', river_km: 3, where: 'below Wharncliffe WwTW', river: 'Don' }).say,
    /^Measured below Wharncliffe WwTW on the Don, 3\.0&nbsp;km upstream, /);
});

test('the age is counted when the page is read, not when it was built', () => {
  assert.match(waterTile({ ...bures, observed_at: hoursAgo(26) }).say, /, 26 h ago\.$/);
  assert.match(waterTile({ ...bures, observed_at: hoursAgo(0.5) }).say, /, 30 min ago\.$/);
});

test('without a distance along the river, a direction or a time, nothing is shown', () => {
  assert.equal(waterTile(null), null);
  assert.equal(waterTile(undefined), null);
  assert.equal(waterTile({ ...bures, river_km: null }), null);
  assert.equal(waterTile({ ...bures, direction: null }), null);
  assert.equal(waterTile({ ...bures, observed_at: null }), null);
  assert.equal(waterTile({ ...bures, temp_c: null }), null);
});

test('only the Environment Agency is linked, and the words are escaped', () => {
  assert.doesNotMatch(waterTile({ ...bures, url: 'https://example.org/x' }).more, /href=/);
  assert.match(waterTile({ ...bures, where: 'at <b>Mill</b>' }).say, /at &lt;b&gt;Mill&lt;\/b&gt;/);
  assert.doesNotMatch(waterTile({ ...bures, quality: 'Good' }).more, /not yet checked/);
});
