// Click anywhere (src/dipcast/site/anypoint.js) against the Python it follows. Two fixtures:
//  - anypoint_spot.json: a listed spot's forecast from forecast_point and its inputs
//    (tests/fixtures/make_anypoint_spot.py); the page's arithmetic must give the same days to
//    three decimals;
//  - anypoint_synthetic.json: a small network's published files and what the API traced for
//    clicks on it (tests/test_anypoint.py, which also checks the fixture against the API).
// On its own: node --test tests/site_anypoint.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const A = require('../src/dipcast/site/anypoint.js');

const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8'));
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} against ${b}`);

test('the page\'s transport arithmetic gives a listed spot\'s forecast to three decimals', () => {
  const fx = fixture('anypoint_spot.json');
  const got = A.forecast(fx.rows, fx.od, fx.cal);
  assert.equal(got.days.length, fx.expect.days.length);
  got.days.forEach((d, k) => {
    const want = fx.expect.days[k];
    assert.equal(d.date, want.date);
    assert.equal(d.risk === null, want.risk === null, `${d.date}: a figure on one side only`);
    if (want.risk !== null) near(d.risk, want.risk, 0.0005 + 1e-9, `${d.date} risk`);
    assert.equal(d.label, want.label, d.date);
    assert.equal(d.data_status, want.data_status, d.date);
    if (want.expected_spilling_overflows !== null) near(d.expected_spilling_overflows, want.expected_spilling_overflows, 0.05 + 1e-9, `${d.date} spilling`);
  });
  near(got.now.risk, fx.expect.now.risk, 0.0005 + 1e-9, 'right now');
  for (const k of ['discharging_upstream', 'recent_upstream', 'monitored_upstream', 'feed_down_upstream']) assert.equal(got.now[k], fx.expect.now[k], k);
  assert.equal(got.upstream_summary.overflows, fx.expect.upstream_summary.overflows);
  assert.equal(got.upstream_summary.history_days, fx.expect.upstream_summary.history_days);
  near(got.upstream_summary.sum_weight, fx.expect.upstream_summary.sum_weight, 0.0005 + 1e-9, 'sum of weights');
});

test('the calibration follows numpy.interp, flat beyond the knots, and keeps an unknown unknown', () => {
  const c = { iso_x: [0, 0.1, 1], iso_y: [0.01, 0.2, 0.9] };
  near(A.calibrate(0.05, c), 0.105, 1e-12, 'between knots');
  assert.equal(A.calibrate(-1, c), 0.01);
  assert.equal(A.calibrate(2, c), 0.9);
  assert.ok(Number.isNaN(A.calibrate(NaN, c)));
  // A spill a day and a half upstream lands half on each of the next two days (transport.shift_by_travel).
  assert.deepEqual(A.shiftByTravel([[1, 0, 0, 0]], [36]), [[0, 0.5, 0.5, 0]]);
  assert.equal(A.historyDays([]), 1);
  assert.equal(A.historyDays([33.3]), 3);
});

// The synthetic network's files, as the page would fetch them.
const syn = fixture('anypoint_synthetic.json');
const files = (() => {
  const tiles = {};
  for (const [k, t] of Object.entries(syn.files.tiles)) {
    const b = Buffer.from(t.index, 'base64');
    tiles[k] = { index: A.decodeIndex(b.buffer.slice(b.byteOffset, b.byteOffset + b.length)), links: t.links };
  }
  return { cfg: syn.files.cfg, lakes: syn.files.lakes, od: syn.files.od, tiles };
})();
const placeAt = c => {
  const keys = A.lakePolygon(c.lat, c.lon, files.lakes, files.cfg.lake_shore_m) ? [] : A.squares(c.lat, c.lon, files.cfg);
  return A.place(c.lat, c.lon, { ...files, tiles: Object.fromEntries(keys.map(k => [k, files.tiles[k]])) });
};

test('a click goes where the API puts it, and gets the API\'s overflows, distances and dilutions', () => {
  let rows = 0;
  for (const c of syn.clicks.filter(x => x.what !== 'the lonely pool')) {
    const got = placeAt(c);
    assert.equal(got.mode, c.mode, c.what);
    if (c.mode === 'none') { assert.match(got.error, /No river or lake within 1\.5 km/, c.what); continue; }
    assert.equal(got.location.watercourse, c.watercourse, c.what);
    assert.equal(!!got.location.adopted_main_channel, c.adopted, `${c.what}: moved to the main channel`);
    const mine = got.rows.map(r => ({ ...r, site_id: syn.files.ids[r.i] })).sort((a, b) => a.site_id.localeCompare(b.site_id));
    const want = [...c.rows].sort((a, b) => a.site_id.localeCompare(b.site_id));
    assert.deepEqual(mine.map(r => r.site_id), want.map(r => r.site_id), c.what);
    mine.forEach((r, k) => {
      // Distances in tens of metres, the click's place along a line simplified to 30 m.
      near(r.dist, want[k].distance_m, 6, `${c.what} ${r.site_id} distance`);
      near(r.dlake, want[k].lake_distance_m, 3, `${c.what} ${r.site_id} across the lake`);
      near(r.dil, want[k].dilution, 1e-3, `${c.what} ${r.site_id} dilution`);
      rows++;
    });
  }
  assert.ok(rows > 50, `${rows} rows compared`);
});

test('a click in a lake with no river connection says so', () => {
  const got = placeAt(syn.clicks.find(x => x.what === 'the lonely pool'));
  assert.equal(got.mode, 'isolated');
  assert.equal(got.name, 'Lonely Pool');
  assert.match(got.error, /^An isolated lake with no river connection/);
});

test('a click outside every square with data says there is no data there', () => {
  const got = A.place(52.0, -4.0, { ...files, tiles: {} });
  assert.equal(got.mode, 'none');
  assert.match(got.error, /England only/);
});

test('a point with no overflow upstream gets the API\'s answer: every day zero, nothing upstream', () => {
  const od = { today: 1, days: ['2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07'], scale: 1000,
    p: [], live: [], status: [], company: [], companies: [], low: [],
    assumptions: { river_velocity_ms: 0.5, lake_velocity_ms: 0.05, t90_hours: 30, low_confidence_factor: 0.7, max_missing_share: 0.1 } };
  const got = A.forecast([], od, {});
  assert.deepEqual(got.days.map(d => [d.date, d.risk, d.label]), od.days.slice(1).map(d => [d, 0, 'low']));
  assert.equal(got.upstream_summary.overflows, 0);
});
