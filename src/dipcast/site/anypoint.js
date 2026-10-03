// Click anywhere: a forecast for a point on the map that is not a listed spot, worked out in the
// browser from the files the build publishes under data/anypoint/ (scripts/build_any_point.py says
// what is in each). The steps are the API's, in the same order:
//  - where the click goes (model/transport.py, _locate): inside or within 150 m of a WFD lake, that
//    lake; else the nearest link within 1.5 km, or the nearest lake centreline if it is less than
//    twice as far; a click on a small side channel near a much bigger river is traced as that
//    river (_adopt_main_channel);
//  - each upstream overflow's distance and dilution (upstream_overflows), from the link's record
//    for the click's place along it, or from the lake's inlets and the straight line across;
//  - travel at the river speed (the lake speed across a lake), die-off 10^(-t / T90), weight =
//    die-off x dilution, less for an overflow placed by proximity alone;
//  - each day's spill probability through the lead calibration (forecast.calibrate_by_lead),
//    moved later by the travel time and combined, 1 - prod(1 - p w) (transport.combine_daily),
//    with no figure where over 10% of the weight has no rain data (missing_share);
//  - right now from the live status (live_now_risk).
// No E. coli estimate: it needs the rain at the spot itself, which only the build fetches, for the
// listed spots. The card is the listed spot's card (index.html, render), marked as unlisted.
// A plain script, as levels.js: one global, AnyPoint; in Node the last lines export it.
const AnyPoint = (() => {
  const BASE = 'data/anypoint/';
  const FORMS = ['inlandRiver', 'tidalRiver', 'lake', 'canal'], LAKE = 2, RIVER_FORMS = [0, 1];
  const ADOPT = { radius: 500, min: 5000, ratio: 5 };   // transport.ADOPT_RADIUS_M, ADOPT_MIN_M, ADOPT_RATIO
  const BASE_UNIT = 10, SCALE = 10000;                  // build_any_point.BASE_UNIT_M, DIL_SCALE
  const DAYS_AHEAD = 4, KEEP = 10;                       // forecast_point's days, build_site.KEEP_CONTRIBUTORS
  const RAD = Math.PI / 180, A_ELL = 6378137, E2 = 0.00669438;   // WGS84

  // ------------------------------------------------------------------ the arithmetic
  // transport.history_days: the days before today the grid must start for the longest travel time.
  const historyDays = travel => travel.length ? Math.max(1, Math.ceil(Math.max(...travel) / 24) + 1) : 1;
  // numpy.interp: flat beyond the ends.
  function interp(x, xp, fp) {
    if (Number.isNaN(x)) return NaN;
    if (x <= xp[0]) return fp[0];
    const n = xp.length;
    if (x >= xp[n - 1]) return fp[n - 1];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xp[m] <= x) lo = m; else hi = m; }
    return fp[lo] + (fp[hi] - fp[lo]) * (x - xp[lo]) / (xp[hi] - xp[lo]);
  }
  // forecast._apply: the isotonic knots where fitted, else Platt.
  function calibrate(p, c) {
    if (!c) return p;
    if (c.iso_x) return interp(p, c.iso_x, c.iso_y);
    if (Number.isNaN(p)) return NaN;
    const q = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
    return 1 / (1 + Math.exp(-(c.a + c.b * Math.log(q / (1 - q)))));
  }
  // transport.shift_by_travel: each overflow's days later by its travel time, a fraction split.
  function shiftByTravel(P, travel) {
    return P.map((row, i) => {
      const d = row.length, s = travel[i] / 24, k = Math.floor(s), a = s - k, out = new Array(d).fill(0);
      for (let j = 0; j < d; j++) {
        if (j + k < d) out[j + k] += (1 - a) * row[j];
        if (j + k + 1 < d) out[j + k + 1] += a * row[j];
      }
      return out;
    });
  }
  // transport.combine_daily: 1 - prod(1 - p w), a day with no rain data counting as no spill.
  function combineDaily(P, w, travel, nd) {
    if (!P.length) return new Array(nd).fill(0);
    const eff = shiftByTravel(P.map(row => row.map(x => Number.isNaN(x) ? 0 : x)), travel);
    return eff[0].map((_, j) => 1 - eff.reduce((acc, row, i) => acc * (1 - Math.min(1, Math.max(0, row[j])) * w[i]), 1));
  }
  // transport.missing_share: the share of the weight arriving from days with no rain data.
  function missingShare(avail, w, travel, nd) {
    const tot = w.reduce((a, b) => a + b, 0);
    if (!avail.length || tot <= 0) return new Array(nd).fill(0);
    const miss = shiftByTravel(avail.map(row => row.map(ok => ok ? 0 : 1)), travel);
    return miss[0].map((_, j) => Math.min(1, Math.max(0, miss.reduce((a, row, i) => a + row[j] * w[i], 0) / tot)));
  }
  const riskLabel = r => r < 0.15 ? 'low' : r < 0.4 ? 'moderate' : r < 0.7 ? 'high' : 'very high';   // transport.risk_label
  const r3 = x => Math.round(x * 1000) / 1000, r1 = x => Math.round(x * 10) / 10;

  // A link record (links/<t>.json) at a click `frac` along the link: [{i, dist, dil}], as
  // upstream_overflows gives them (build_any_point.rows_at and Tracer say how).
  function rowsAt(rec, frac, cap, l0) {
    const offset = frac * rec.n, shrink = (rec.u + l0) / (rec.u + l0 + offset), out = [];
    let i = 0;
    for (let k = 0; k < (rec.o || []).length; k += 3) {
      i += rec.o[k];
      const dist = rec.o[k + 1] * BASE_UNIT + offset;
      if (dist <= cap) out.push({ i, dist, dlake: 0, dil: Math.min(1, rec.o[k + 2] / SCALE * shrink) });
    }
    for (let k = 0; k < (rec.s || []).length; k += 2)
      if (rec.s[k + 1] < offset) out.push({ i: rec.s[k], dist: offset - rec.s[k + 1], dlake: 0, dil: Math.min(1, shrink) });
    return out;
  }
  // A lake's inlets (lakes.json) for a click: the river distance to the inlet, then the straight
  // line across; a WFD lake's area divides the dilution by 1 + area / 5 km2.
  function lakeRows(lake, at, area, a0) {
    const out = [], xy = frame(at[0], at[1]), div = area ? 1 + area / a0 : 1;
    for (const inlet of lake.inlets) {
      const [x, y] = xy(inlet.at[1], inlet.at[0]), dlake = Math.hypot(x, y);
      for (let k = 0; k < inlet.o.length; k += 3) out.push({ i: inlet.o[k], dist: inlet.o[k + 1], dlake, dil: inlet.o[k + 2] / SCALE / div });
    }
    return out;
  }

  // The forecast for upstream rows, as forecast_point builds it (gauge off, as for the listed
  // spots). od: overflow_days.json; cal: lead_calibration.leads; names: overflow_ids.json, for the
  // overflows that matter most (optional).
  function forecast(rows, od, cal, names = null) {
    const a = od.assumptions, low = new Set(od.low), n = rows.length;
    const travel = rows.map(r => (r.dist / a.river_velocity_ms + r.dlake / a.lake_velocity_ms) / 3600);
    const w = rows.map((r, k) => Math.pow(10, -travel[k] / a.t90_hours) * r.dil * (low.has(r.i) ? a.low_confidence_factor : 1));
    const hist = historyDays(travel), nd = hist + DAYS_AHEAD + 1, t0 = od.today;
    const P = rows.map(r => Array.from({ length: nd }, (_, j) => {
      const v = od.p[r.i][t0 - hist + j]; return v === null || v === undefined ? NaN : v / od.scale; }));
    const avail = P.map(row => row.map(x => !Number.isNaN(x)));
    const leads = Object.keys(cal || {}).map(Number), kmax = leads.length ? Math.max(...leads) : 0;
    const Pc = leads.length ? P.map(row => row.map((x, j) => calibrate(x, cal[String(Math.min(Math.max(j - hist, 0), kmax))]))) : P;
    const risk = combineDaily(Pc, w, travel, nd), miss = missingShare(avail, w, travel, nd);
    const days = [];
    for (let j = hist; j < nd; j++) {
      const known = !(miss[j] > a.max_missing_share), date = od.days[t0 + j - hist];
      const exp = Pc.reduce((s, row) => s + (Number.isNaN(row[j]) ? 0 : row[j]), 0);
      days.push({ date, risk: known ? r3(risk[j]) : null, label: known ? riskLabel(risk[j]) : 'unknown',
        expected_spilling_overflows: known ? r1(exp) : null,
        data_status: !known ? 'rain unavailable' : miss[j] > 0 ? 'partial' : 'ok', rain_missing_share: r3(miss[j]),
        in_validated_season: [5, 6, 7, 8, 9].includes(Number(date.slice(5, 7))) });
    }
    // Right now: transport.live_now_risk, and forecast.live_counts.
    const status = rows.map(r => od.status[r.i]), extra = rows.map(r => od.live[r.i] / od.scale);
    const contrib = rows.map((_, k) => w[k] * extra[k]);
    const nowRisk = 1 - contrib.reduce((acc, c) => acc * (1 - Math.min(1, Math.max(0, c))), 1);
    const down = status.map(s => s === -3), hasLive = status.map(s => s !== -2);
    const monitored = hasLive.filter((h, k) => h && !down[k]).length, fd = {};
    rows.forEach((r, k) => { if (down[k]) { const c = od.companies[od.company[r.i]]; fd[c] = (fd[c] || 0) + 1; } });
    const now = { risk: r3(nowRisk), label: riskLabel(nowRisk), discharging_upstream: status.filter(s => s === 1).length,
      recent_upstream: contrib.filter((c, k) => c > 0 && status[k] !== 1 && !down[k]).length, monitored_upstream: monitored,
      feed_down_upstream: down.filter(Boolean).length,
      feed_down: Object.keys(fd).sort().map(c => ({ company: c, overflows: fd[c], since: (od.feed_down_since || {})[c] ?? null })) };
    // The overflows that matter most: forecast_point's relevance, the build's ten.
    const ahead = Pc.map(row => row.slice(hist, hist + DAYS_AHEAD + 1));
    const order = rows.map((_, k) => k).sort((x, y) => relevance(y) - relevance(x)).slice(0, KEEP);
    function relevance(k) { return Math.max(contrib[k], w[k] * Math.max(0, ...ahead[k].map(x => Number.isNaN(x) ? 0 : x)), 0.1 * w[k]); }
    const contributors = order.map(k => { const r = rows[k], i = r.i, pd = ahead[k];
      return { site_id: names ? names.ids[i] : String(i), site_name: names ? names.name[i] : null, company: od.companies[od.company[i]],
        lat: names ? names.lat[i] : null, lon: names ? names.lon[i] : null, status: status[k], has_live: hasLive[k],
        snap_confidence: low.has(i) ? 'low' : null, distance_km: r1(r.dist / 1000), lake_distance_km: r1(r.dlake / 1000),
        travel_h: r1(travel[k]), weight: r3(w[k]), p_spill_today: r3(Number.isNaN(pd[0]) ? 0 : pd[0]),
        p_spill_tomorrow: r3(Number.isNaN(pd[1]) ? 0 : pd[1]), p_spill_days: pd.map(x => Number.isNaN(x) ? null : r3(x)),
        now_contribution: r3(contrib[k]), rain_h: od.rain_h ? od.rain_h[i] : null }; });
    return { now, days, contributors, upstream_summary: { overflows: n, with_live_feed: monitored, without_live_feed: n - monitored,
      sum_weight: r3(w.reduce((s, x) => s + x, 0)), history_days: hist, max_travel_h: n ? r1(Math.max(...travel)) : 0 },
      // The oldest rain forecast behind a weight that counts, in hours (a cell is fetched on a rota).
      rain_h: Math.max(0, ...rows.map((r, k) => w[k] >= 0.001 && od.rain_h && od.rain_h[r.i] !== null ? od.rain_h[r.i] : 0)) };
  }

  // ------------------------------------------------------------------ where a click goes
  // Metres east and north of (lat0, lon0) on a flat local frame, as the British National Grid
  // measures them (the API's distances are grid metres): the ellipsoid's radii of curvature at that
  // latitude, times the grid's scale factor that far from its central meridian (2° W). Within 2 km
  // it is a few centimetres off.
  function frame(lat0, lon0) {
    const s = Math.sin(lat0 * RAD), w = 1 - E2 * s * s, n = A_ELL / Math.sqrt(w), m = A_ELL * (1 - E2) / (w * Math.sqrt(w));
    const east = (lon0 + 2) * RAD * n * Math.cos(lat0 * RAD), k = 0.9996012717 * (1 + east * east / (2 * n * m));
    const ky = m * RAD * k, kx = n * Math.cos(lat0 * RAD) * RAD * k;
    return (lat, lon) => [(lon - lon0) * kx, (lat - lat0) * ky];
  }
  // The nearest point of a line to the frame's origin: distance, and fraction along the line.
  function nearestOnLine(pts) {
    let best = Infinity, along = 0, tot = 0;
    for (let k = 0; k + 1 < pts.length; k++) {
      const [x0, y0] = pts[k], [x1, y1] = pts[k + 1], dx = x1 - x0, dy = y1 - y0, l2 = dx * dx + dy * dy, len = Math.sqrt(l2);
      const t = l2 > 0 ? Math.min(1, Math.max(0, -(x0 * dx + y0 * dy) / l2)) : 0, d = Math.hypot(x0 + t * dx, y0 + t * dy);
      if (d < best) { best = d; along = tot + t * len; }
      tot += len;
    }
    return { d: best, frac: tot > 0 ? along / tot : 0 };
  }
  // link_index/<t>.bin (build_any_point.pack_link_index).
  function decodeIndex(buf) {
    const h = new Int32Array(buf, 0, 6);
    if (h[0] !== 0x494C5353 || h[1] !== 1) throw new Error('unknown link index');
    const n = h[2], nv = h[3], nb = h[4];
    let off = 24;
    const take = (T, cnt) => { const a = new T(buf, off, cnt); off += 4 * cnt; return a; };
    const lno = take(Int32Array, n), meta = take(Int32Array, n), upstream = take(Float32Array, n), offsets = take(Int32Array, n + 1);
    const ll = take(Float32Array, 2 * nv);
    const names = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, off, nb)));
    return { n, lno, meta, upstream, offsets, ll, names };
  }
  // Every link in the squares given, measured from the click: one entry per link number.
  function candidates(lat, lon, tiles) {
    const xy = frame(lat, lon), seen = new Map();
    for (const [key, t] of Object.entries(tiles)) {
      const ix = t.index;
      for (let k = 0; k < ix.n; k++) {
        if (seen.has(ix.lno[k])) continue;
        const pts = [];
        for (let v = ix.offsets[k]; v < ix.offsets[k + 1]; v++) pts.push(xy(ix.ll[2 * v + 1], ix.ll[2 * v]));
        const { d, frac } = nearestOnLine(pts), nm = (ix.meta[k] >> 2) - 1;
        seen.set(ix.lno[k], { lno: ix.lno[k], tile: key, form: ix.meta[k] & 3, name: nm >= 0 ? ix.names[nm] : null, upstream: ix.upstream[k], d, frac });
      }
    }
    return [...seen.values()];
  }
  // Ray casting, and the distance in metres to a polygon's outline.
  function inRing(lon, lat, ring) {
    let inside = false;
    for (let a = 0, b = ring.length - 1; a < ring.length; b = a++) {
      const [xa, ya] = ring[a], [xb, yb] = ring[b];
      if ((ya > lat) !== (yb > lat) && lon < (xb - xa) * (lat - ya) / (yb - ya) + xa) inside = !inside;
    }
    return inside;
  }
  function polygonDistance(lat, lon, poly) {
    if (poly.rings.some(r => inRing(lon, lat, r))) return 0;
    const xy = frame(lat, lon);
    return Math.min(...poly.rings.map(r => nearestOnLine(r.map(([x, y]) => xy(y, x))).d));
  }
  // transport._lake_polygon_at: the WFD lake the click is in, or within the shore distance of.
  function lakePolygon(lat, lon, lakes, shore) {
    const pad = shore / 111000, padx = pad / Math.cos(lat * RAD);
    let best = null;
    for (const p of lakes.polygons) {
      const [w, s, e, n] = p.bbox;
      if (lon < w - padx || lon > e + padx || lat < s - pad || lat > n + pad) continue;
      const d = polygonDistance(lat, lon, p);
      if (d <= shore && (!best || d < best.d)) best = { poly: p, d };
    }
    return best;
  }
  // transport._locate's second step, and _adopt_main_channel: river or lake centreline, and the
  // link a river click is traced along. cands: candidates(); cfg: tiles.json.
  function locate(cands, cfg) {
    const near = (pred, max) => cands.filter(c => pred(c) && c.d <= max).reduce((a, b) => !a || b.d < a.d ? b : a, null);
    const river = near(() => true, cfg.snap_m), lake = near(c => c.form === LAKE, cfg.lake_snap_m);
    if (lake && (!river || river.form === LAKE || lake.d * cfg.lake_bias < river.d)) return { mode: 'lake', link: lake };
    if (!river) return { mode: 'none' };
    let target = river, adopted = false;
    if (river.upstream < ADOPT.min) {
      const alt = cands.filter(c => RIVER_FORMS.includes(c.form) && c.lno !== river.lno && c.d <= ADOPT.radius)
        .reduce((a, b) => !a || b.upstream > a.upstream || (b.upstream === a.upstream && b.d < a.d) ? b : a, null);
      if (alt && alt.upstream >= Math.max(ADOPT.min, ADOPT.ratio * river.upstream)) { target = alt; adopted = true; }
    }
    return { mode: 'river', link: target, adopted };
  }

  // ------------------------------------------------------------------ the files
  const tileKey = (lat, lon, deg) => `${Math.floor(lat / deg)}_${Math.floor(lon / deg)}`;
  const memo = new Map();
  const json = (name, fresh = false) => {
    if (fresh) memo.delete(name);
    if (!memo.has(name)) memo.set(name, fetch(BASE + name, { cache: 'no-cache' }).then(r => {
      if (!r.ok) throw new Error(`${name} answered ${r.status}`); return r.json(); }).catch(e => { memo.delete(name); throw e; }));
    return memo.get(name);
  };
  const tile = async (key, cfg) => {
    const name = `tile:${key}`;
    if (!memo.has(name)) memo.set(name, Promise.all([
      fetch(`${BASE}link_index/${key}.bin?n=${cfg.network}`).then(r => { if (!r.ok) throw new Error(`square ${key} answered ${r.status}`); return r.arrayBuffer(); }),
      json(`links/${key}.json`)]).then(([buf, links]) => ({ index: decodeIndex(buf), links: links.links, ids: links.ids }))
      .catch(e => { memo.delete(name); throw e; }));
    return memo.get(name);
  };
  // The squares within the snap distance of a click that have files.
  function squares(lat, lon, cfg) {
    const dy = (cfg.snap_m + 100) / 111000, dx = dy / Math.cos(lat * RAD), have = new Set(cfg.tiles), out = new Set();
    for (const a of [lat - dy, lat, lat + dy]) for (const b of [lon - dx, lon, lon + dx]) { const k = tileKey(a, b, cfg.tile_deg); if (have.has(k)) out.add(k); }
    return [...out];
  }

  // Where a click goes and the overflows upstream of it, from the files: {mode, kind, name,
  // location, rows} or, where there is no forecast, {error}. files: {cfg: tiles.json, lakes:
  // lakes.json, od: overflow_days.json, tiles: {square: {index, links}}} for the squares near it.
  const ISOLATED = 'An isolated lake with no river connection in the network: storm overflows cannot reach it by water, '
    + 'so the forecast has nothing to say about it. Risk from wildlife, runoff and bathers is not modelled.';
  function place(lat, lon, files) {
    const { cfg, lakes, od, tiles } = files, a = od.assumptions;
    const poly = lakePolygon(lat, lon, lakes, cfg.lake_shore_m);
    if (poly) {
      const p = poly.poly, name = p.name || 'Unnamed lake';
      if (p.lake === -1) return { mode: 'isolated', kind: 'lake', name, error: ISOLATED };
      return { mode: 'lake', kind: 'lake', name, rows: p.lake === null ? [] : lakeRows(lakes.lakes[p.lake], [lat, lon], p.area_km2, a.lake_area_halving_km2),
        location: { mode: 'lake', watercourse: p.name, lake_area_km2: Math.round(p.area_km2 * 10) / 10, lake_source: 'polygon', form: 'lake' } };
    }
    if (!Object.keys(tiles).length) return { mode: 'none', kind: '', name: 'This point', error: 'SwimSignal has overflow data for England only, so it has no forecast here.' };
    const at = locate(candidates(lat, lon, tiles), cfg);
    if (at.mode === 'none') return { mode: 'none', kind: '', name: 'This point', error: 'No river or lake within 1.5 km of this point.' };
    const entry = tiles[at.link.tile].links[String(at.link.lno)], lake = at.mode === 'lake';
    const rows = !entry ? [] : lake ? (entry.lake === undefined ? [] : lakeRows(lakes.lakes[entry.lake], [lat, lon], null, a.lake_area_halving_km2))
      : rowsAt(entry, at.link.frac, a.max_upstream_km * 1000, a.l0_m);
    return { mode: at.mode, kind: at.mode, name: at.link.name || (lake ? 'Unnamed lake' : 'Unnamed watercourse'), rows, link: at.link,
      location: { mode: at.mode, watercourse: at.link.name, snap_distance_m: Math.round(at.link.d), form: FORMS[at.link.form],
        lake_source: lake ? 'centreline' : null, adopted_main_channel: !!at.adopted } };
  }

  // A point's forecast in the shape of a spot in spots.json, for the page's own card. The files of
  // one build name overflows by the same list; a deploy between two fetches is fetched again once.
  async function point(lat, lon, again = true) {
    const [cfg, lakes, od] = await Promise.all([json('tiles.json'), json('lakes.json'), json('overflow_days.json')]);
    const inLake = lakePolygon(lat, lon, lakes, cfg.lake_shore_m), keys = inLake ? [] : squares(lat, lon, cfg);
    const tiles = Object.fromEntries(await Promise.all(keys.map(async k => [k, await tile(k, cfg)])));
    const at = place(lat, lon, { cfg, lakes, od, tiles });
    const rows = at.rows || [];
    const ids = rows.length ? await json('overflow_ids.json') : null;
    const versions = new Set([cfg.ids, od.ids, lakes.ids, ...Object.values(tiles).map(t => t.ids), ...(ids ? [ids.ids_version] : [])]);
    if (versions.size > 1) {
      if (!again) throw new Error('the forecast files changed while loading; try again in a minute');
      memo.clear();
      return point(lat, lon, false);
    }
    const base = { id: `point-${lat.toFixed(5)},${lon.toFixed(5)}`, unlisted: true, lat, lon, source: 'unlisted', name: at.name, kind: at.kind };
    if (at.error) return { ...base, error: at.error, location: { mode: at.mode }, days: [], contributors: [], now: { risk: 0, label: 'unknown' } };
    const verif = await fetch('data/verification.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
    const cal = ((verif || {}).lead_calibration || {}).leads || {};
    // No rows is the API's answer where no monitored overflow is upstream: every day 0.
    const f = forecast(rows, od, cal, ids);
    const spot = (typeof DATA !== 'undefined' && DATA && DATA.spots.find(s => s.assumptions)) || {};
    const a = od.assumptions;
    return { ...base, location: at.location, ...f,
      assumptions: { river_velocity_ms: a.river_velocity_ms, lake_velocity_ms: a.lake_velocity_ms, t90_hours: a.t90_hours,
        max_upstream_km: a.max_upstream_km, recent_spill_hours: a.recent_spill_hours, lead_calibration: Object.keys(cal).length > 0,
        model: (spot.assumptions || {}).model, version: (spot.assumptions || {}).version } };
  }

  // ------------------------------------------------------------------ the page
  // The spot card (index.html, render) for the point, then what differs for an unlisted point: the
  // mark, no Save, no share or swim log, and the request in their place.
  const REQUEST = 'https://github.com/ethanbuckley/swimsignal/issues/new';
  function request(d) {
    const q = new URLSearchParams({ template: 'spot-request.yml', title: `Spot request: ${d.name}`, name: d.name,
      location: `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}`, kind: d.kind === 'lake' ? 'lake' : 'river' });
    return `<div class="actions"><a class="btn primary" href="${REQUEST}?${esc(q.toString())}" rel="noopener">Request this as a spot</a></div>`
      + '<p class="small muted below">Opens a request on GitHub with this point\'s position filled in. A listed spot is checked by hand and gets its own rain forecast and E.&nbsp;coli estimate.</p>';
  }
  function show(d, push) {
    current = d; DAY = null; shown = null;
    render(d);
    const box = document.getElementById('result');
    const save = box.querySelector('#save'); if (save) save.remove();
    const kind = box.querySelector('.kind');
    if (kind) kind.innerHTML = ['Unlisted point: not hand-checked', ...kind.innerHTML.split(' · ').filter(t => t !== 'Unlisted')].join(' · ');
    const acts = box.querySelector('.actions');
    if (acts) {   // the spot's share, picture and swim-log buttons, and their notes
      let el = acts.nextElementSibling;
      while (el && el.matches('p.below')) { const nx = el.nextElementSibling; el.remove(); el = nx; }
      if (d.error && d.location.mode !== 'isolated') acts.remove(); else acts.outerHTML = request(d);
    }
    if (d.rain_h >= 2) { const iss = box.querySelector('#answer-words .issued');
      if (iss) iss.insertAdjacentHTML('afterend', `<p class="issued">Uses rain forecasts up to ${d.rain_h} hours older than that</p>`); }
    selLayer.clearLayers();
    L.circleMarker([d.lat, d.lon], { radius: 14, color: RING(), weight: 3, fill: false, interactive: false }).addTo(selLayer);
    scrollTop();
    if (push) focusHeading();
  }
  const busy = h => { const from = (history.state || {}).from;   // the back link as render names it
    setPanel(`<div class="top-row"><a href="${esc(from === 'saved' ? savedPath() : ROOT.pathname)}" class="back" id="back">`
      + `${from === 'saved' ? 'Saved spots' : from === 'map' ? 'Map' : 'All spots'}</a></div>${h}`); };
  let ticket = 0;
  async function open(lat, lon, push = true) {
    const mine = ++ticket;
    // A history entry, as opening a spot makes (index.html, select): Back returns to the list, the
    // Saved page or the full map as they were.
    const fromMap = mapMode();
    if (push) {
      keepPlace();
      if (fromMap) { const c = map.getCenter(); history.replaceState({ ...history.state, mapView: [c.lat, c.lng, map.getZoom()] }, ''); }
      history.pushState({ point: [lat, lon], from: fromMap ? 'map' : shown }, '', ROOT.pathname);
    }
    if (fromMap) setMapMode(false);
    busy('<p class="muted" aria-live="polite">Working out the forecast for this point…</p>'); bindBack();
    try {
      const d = await point(lat, lon);
      if (mine === ticket) show(d, push);
    } catch (e) {
      if (mine === ticket) { busy(`<p class="notice">Could not make a forecast for this point: ${esc(e.message)}.</p>`); bindBack(); }
    }
  }
  // A click on the map away from a listed spot. On a phone only the full map takes one: the small
  // maps on a spot's page are for looking.
  function onMapClick(e) {
    if (typeof DATA === 'undefined' || !DATA || (PHONE.matches && !mapMode())) return;
    const at = map.latLngToContainerPoint(e.latlng);
    for (const s of DATA.spots) if (map.latLngToContainerPoint([s.lat, s.lon]).distanceTo(at) < 14) return;   // the marker's own click opens it
    open(e.latlng.lat, e.latlng.lng);
  }

  return { historyDays, interp, calibrate, shiftByTravel, combineDaily, missingShare, riskLabel, rowsAt, lakeRows, forecast,
    frame, nearestOnLine, decodeIndex, candidates, inRing, polygonDistance, lakePolygon, locate, place, tileKey, squares, point, open, onMapClick };
})();

if (typeof window !== 'undefined' && typeof map !== 'undefined') {
  map.on('click', AnyPoint.onMapClick);
  // Forward to a point opened here: the page's own handler draws the list first.
  window.addEventListener('popstate', () => { const p = (history.state || {}).point; if (p && DATA) AnyPoint.open(p[0], p[1], false); });
}
if (typeof module === 'object' && module.exports) module.exports = AnyPoint;
