# SwimSignal roadmap, October 2026

Written 3 October 2026 in answer to two questions from Ethan: "how come we only have 89 swim
spots, could we have more?" and "how can we build this out so it is a seriously useful app and
not just a gimmick?" Every number below was measured or read today unless marked *inferred*.
Section 6 lists the commands and pages the numbers came from. Section 4 holds tasks written
so that an agent can take one without this document's context.

## 1. Summary: what to do first and why

The spot count is the wrong thing to fix first. The site already forecasts any point on the
river network when the API runs; the static site lists 89 because each listed spot costs a
hand check of where it sits on the network. Adding spots one list at a time will not get past a
few hundred, and no community list can be copied: the Outdoor Swimming Society and the Wild
Swimming books reserve all rights, and OpenStreetMap's licence forces any file that mixes its
spots with ours under ODbL. The way out is to make "any point" work on the static site. That
turns out to be cheap: 82% of inland river links have no monitored overflow within 60 km of
them upstream, the rest average 2.5, so a precomputed table of every link's upstream overflows
is about 6 MB, and the daily spill probabilities for all 14,632 overflows need 33 rain
requests instead of today's 6. Both fit GitHub Pages and the free Open-Meteo tier.

Recommended order:

1. **Fix the live bug.** Today's build (12:06 UTC, 3 Oct) failed at 4 of 89 spots, including
   the Nidd at Knaresborough, a designated bathing water. Cause: a water company feed listed
   one overflow twice and the spill matrix pivots on the overflow id. One line and a test
   (task 1).
2. **Say the right thing where the model has nothing to say.** 17 spots have no monitored
   overflow upstream and 2 lakes have no river connection. They show "No monitored overflows
   upstream" or "Not covered". A swimmer reads either as "no information". The honest reading is
   "no sewage risk from monitored overflows; algae, rating, wildlife and runoff still apply",
   with whatever the site does know (rating, algae check, river level, rain) kept in view
   (task 2).
3. **Click anywhere on the static site.** Publish the per-link upstream table and the daily
   per-overflow probabilities as static files, then let the page compute a forecast for any
   click (tasks 3 and 4). This answers "where to go" for every river in England and makes the
   spot list a set of named, checked examples rather than the whole product.
4. **Add spots in the one licensed way that scales.** Review the ~100 OpenStreetMap candidates
   by hand into a separate ODbL file (task 5), and automate the spot-request issue so a request
   is snapped, checked and answered without a maintainer (task 6). Expect 40 to 60 usable OSM
   spots, not thousands.
5. **Earn trust before adding features.** A methods page, the daily scored CSV published, and
   a side-by-side with the Environment Agency's own pollution risk forecast at the inland
   bathing waters where both exist (task 7). The live table already beats climatology on
   19,015 overflow-days; nobody can see that without reading the Accuracy page.
6. **The "should I get in?" layer.** River too high (EA level index and flood warnings, task 8),
   water temperature where the EA Hydrology API has a live sensor (102 inland sensors reported
   in the last week, task 9), and blue-green algae reports once UKCEH confirms a licence
   (open question).
7. **Then** the "best day near me" push, Scotland, and the rest of section 3. Native apps,
   Wallet passes and Siri need a paid Apple account and a native app; not now.

Everything above stays free to run. The one thing that would not is deploying the FastAPI
server, because the network takes about 900 MB of memory, and the static click-anywhere path
removes the main reason to deploy it.

## 2. Part 1: more spots

### 2.1 Where the 89 come from and what they cost

| | Count |
|---|---|
| Rows in spots.csv | 89 |
| Environment Agency designated inland bathing waters | 38 (20 rivers, 18 lakes) |
| Hand-curated rivers and lakes, September 2026 | 51 |
| Kind: river / lake | 58 / 31 |
| Forecast with at least one upstream overflow (3 Oct build) | 66 |
| Forecast possible but no monitored overflow upstream | 17 |
| No forecast possible (isolated lake) | 2 (Cotswold Country Park, Henleaze Lake) |
| Forecast failed in the 3 Oct 12:06 build | 4 (Nidd at Knaresborough, Teme at Ludlow, Severn at Ironbridge, Severn at Shrewsbury) |

Build cost today: the "Build forecasts and site" step took 128 s in the 12:06 run; the whole
job 333 s (the four previous scheduled jobs took 196 to 284 s). The rain prefetch covered 298
cells in one second because the cache was warm. The per-spot work is the network trace (fast),
one EA level-gauge lookup (threaded, 8 workers), and one fiftieth of an Open-Meteo weather call.
Per spot that is well under a second. *Inferred:* 500 spots would add about 2 to 4 minutes to
the build; the 25-minute job limit is far off, and the 6-hour GitHub limit further.

The quality check from PR #56 (`placement_check` in `scripts/build_site.py`) needs the `river`
column filled by hand for each river spot and flags a spot whose snapped watercourse does not
share a word with it, sits over 250 m away, or landed on a lake or canal. It has no check for
lake spots. It scales to hundreds of spots only if the river name comes from the source
(OSM has it on some ways; the EA list has it in the name) and if the check also covers lakes.

### 2.2 The options, with counts

| Option | Would add | Licence and attribution | Placement check | Build time | Recommendation |
|---|---|---|---|---|---|
| (a) OpenStreetMap swim features in England | 402 distinct elements match any swim tag; 111 look like natural water after filtering and 150 m de-duplication; 96 of those look inland; 10 are within 300 m of a spot we have. *Inferred:* 40 to 60 usable after a human reads each one | ODbL. A file that mixes OSM spots with ours becomes a Derivative Database and the whole file must be ODbL (OSMF Collective Database and Horizontal Layers guidelines). Keep OSM spots in their own file with no shared keys, credit "© OpenStreetMap contributors" and link the licence | OSM ways carry `natural=water`/`water=river`; nodes carry nothing. The `river` column must be typed by hand for most | +1 min at most | Do it, by hand, once (task 5). It is the only open source of named inland swim spots |
| (b) Outdoor Swimming Society Wild Swim Map and the Wild Swimming books | OSS map page returns 404 today (taken down in the pandemic per Wikipedia); its terms reserve all rights; Wild Swimming (Daniel Start) is a copyrighted book. Third-party wildswimmap.co.uk shows 649 spots built from EA and OSM data | Not reusable without written permission. Asking OSS means an email to hello@outdoorswimmingsociety.com proposing a credited link-back; they would also want to know what SwimSignal says about safety | n/a | n/a | Do not copy. Ask OSS once for a partnership (open question for Ethan). Link to them from spot pages instead |
| (c) EA designated bathing waters, all | 464 in England: 373 coastal, 53 transitional (estuary), 20 river, 18 lake. We have the 38 inland. Wales (NRW) has 114: 107 coastal, 3 transitional, 2 lake, 2 river. Scotland (SEPA) 90 | OGL v3, already credited | Coastal spots do not sit on the network. The 53 estuary sites sit on tidal river links (11,539 in the network) but the transport model has no tide, no salinity die-off and no validation there | None for the model (it would abstain) | Not as forecast spots. Coastal would need a different model (the EA's PRF uses rain, tide and wind per site); the EA publishes its PRF daily in the same API and SwimSignal should show it, not compete with it (task 7). Estuaries: abstain and show the PRF |
| (d) Rivers Trust and Surfers Against Sewage lists | Rivers Trust publishes the EDM overflow layer (OGL), no swim spots. SAS's Safer Seas and Rivers Service covers 850 UK locations, mostly designated bathing waters, no public API, terms not published for location data | Not available | n/a | n/a | Nothing to take. Both are partners to talk to, not sources |
| (e) Forecast any point on the static site | Every point on 155,779 inland river links and 24,146 lake links | Same data as now | None needed for clicks; listed spots stay checked | +30 to 60 s for all 14,632 overflows (33 rain requests against 6 today; model prediction on 73,000 rows is trivial). Table build is a one-off per network release | Do it (tasks 3 and 4). See 2.3 |
| (f) User-requested spots | The issue template has existed since September; zero requests have been filed | Requester's own words; placement is ours | A GitHub Action can snap, check and comment on each request | Negligible | Automate it (task 6); it costs nothing and tells us where people swim. It will not add volume on its own |

Total realistic growth from lists: 89 today, about 130 to 150 after the OSM review, plus whatever
requests arrive. Clicking anywhere is what removes the ceiling.

### 2.3 Click anywhere without a server: the numbers

The API version forecasts any clicked point by loading the river network (113 MB on disk, about
900 MB in memory, 38 s to load on this Mac) and tracing upstream. That cannot run in a
Cloudflare Worker (128 MB memory, 10 ms CPU on the free plan) and the smallest Fly machine
(256 MB, $2.19 a month) cannot hold it either. So the question is whether the trace can be
precomputed.

Measured today on a sample of 300 random inland river links, with the live overflow table
(14,632 overflows: 9 English companies live, plus Dŵr Cymru's 128 with history only):

| | Value |
|---|---|
| Network links | 193,040 (155,779 inland river, 24,146 lake, 11,539 tidal, 1,576 canal) |
| Upstream overflows within 60 km, per link: mean / median / 90th percentile / max | 2.5 / 0 / 2 / 253 |
| Share of links with none | 82% |
| Rows in a per-link table (link, overflow, distance, dilution) | about 0.5 million; about 6 MB at 12 bytes a row, before compression |
| Rain cells for all overflows | 1,645 (33 requests of 50 cells; today's build uses 298 cells) |
| Static files the site serves today | spots.json 592 KB; overflows.geojson 6.1 MB; verification.json 55 KB |

So the shape is: the build publishes (1) a daily file of spill probabilities per overflow per
day for all 14,632 overflows (about 300 KB), (2) the current live status it already publishes in
`overflows.geojson`, and (3) a once-per-network-release table of each link's upstream overflows
with distance and dilution, split into tiles by grid square so a click downloads a few hundred
KB, not 6 MB. The page then needs only the transport arithmetic (decay, dilution,
1 − ∏(1 − p·w)), which is 30 lines of JavaScript, and the lead calibration knots, which are
already in `verification.json`. Snapping a click to the nearest link needs a lightweight index
of link midpoints (193,040 points, about 2 MB as a packed array) or the same tiles.

Lakes are the hard part: the lake path (route to the lake's inlet, then straight-line across,
area dilution) is per click, but it can be precomputed per lake polygon (564 WFD lakes) as the
set of inlets and their upstream lists, with the straight-line distance done on the page.

*Inferred:* two PR-sized tasks (data, then page), with the gauge-scaled velocity dropped on the
static path as it already is for listed spots. Everything stays within GitHub Pages' 1 GB size
and 100 GB a month soft bandwidth limit, and Open-Meteo's free 10,000 calls a day.

### 2.4 Spots the model cannot help with

19 of 89 spots today get no spill forecast: 17 with no monitored overflow within 60 km
upstream (Hampstead ponds, the Serpentine, Buttermere, Crummock Water, Wastwater, Blea Tarn,
Semerwater, Hatchmere, Colwick West Lake, and the upper reaches at Janet's Foss, Birks Bridge,
Low Force, Slippery Stones, Cadover Bridge, Tarr Steps) and 2 isolated lakes (Cotswold Country
Park, Henleaze Lake). The page says "No monitored overflows upstream" (shown in the clear
colour) or "Not covered by the forecast".

What it should say: a plain level of its own, "No sewage risk from monitored overflows", with a
second line naming what still applies, and the evidence the site does have kept in view: the
EA rating and this season's algae check for designated sites, the river level, and rain in the
last 48 hours (rain at the spot is the one signal that worked on lakes in the E. coli
validation at all, and it is already in `days[].rain_48h_mm`). For the two isolated lakes, say
that the lake has no river inflow in the network, so overflows cannot reach it by water, and
give the same second line. The E. coli estimate should not be shown there: the model was fitted
on sites with overflows upstream and the lake column is already withheld. Task 2 writes this.

## 3. Part 2: from gimmick to useful

### 3.1 What a swimmer decides, and when

A swimmer makes three decisions and SwimSignal should be judged on each:

- **Whether to go** (one to four days ahead): the five-day forecast, the "best day" sentence,
  the push alert. Needs skill at lead 1 to 3, which the verification shows the model has
  (Brier 0.025 to 0.032 against climatology 0.040 on 29 Sep to 2 Oct).
- **Where to go** (the day before, or in the car): coverage and comparison. Today 89 spots and
  "Lower risk nearby". Click anywhere (2.3) is the fix.
- **Whether to get in** (standing at the water): what is happening now, and the hazards the
  model does not cover: level and flow, temperature, algae, and the local signs. This is where
  the site is weakest and where a wrong "low" does the most harm.

Then two layers that make any of it worth trusting: credibility (can a sceptic check it?) and
sustainability (does it keep running without money or attention?).

### 3.2 Candidate features, ranked

Value is to a swimmer at one of the three decisions. Effort: S is one PR, M two or three, L a
project. "API?" says whether it needs the FastAPI server deployed.

| Rank | Feature | Decision it serves | Value | Effort | Data and licence | API? | Notes |
|---|---|---|---|---|---|---|---|
| 1 | Click anywhere on the static site (2.3) | Where to go | High | M (tasks 3, 4) | Existing data | No | Removes the spot ceiling |
| 2 | Honest "no overflows" level (2.4) | Whether to get in | High for 19 spots | S (task 2) | Existing | No | A wrong "no information" today |
| 3 | River too high to swim: level index, flood alerts | Whether to get in | High | S (task 8) | EA flood-monitoring API, OGL; `/id/floods?lat&long&dist` works, severities 1 to 4 | No | Level is already fetched for 75 spots; it is shown, not judged |
| 4 | Methods page, daily scored CSV, comparison with EA PRF | Trust | High | M (task 7) | EA bathing-water API, OGL; PRF retrievable per site per day (`stp-risk-prediction`), 175 sites had one on 15 Sep 2026, season ends 30 Sep | No | Which inland sites get a PRF is to be counted (task 7 does) |
| 5 | Water temperature where a live sensor exists | Whether to get in | Medium | S (task 9) | EA Hydrology API, OGL: 2,012 temperature measures exist but most are old sonde deployments; 102 reported in the last week. Open-Meteo has no inland water temperature; UKCEH lake buoys are archived | No | Show the sensor's distance and age; never estimate where there is none |
| 6 | OSM spots, reviewed | Where to go | Medium | M (task 5) | ODbL, separate file | No | 40 to 60 spots |
| 7 | Spot request automation | Where to go | Medium | S (task 6) | Requester's words | No | Zero requests so far; the automation is to make answering cheap |
| 8 | "Best day this week near me" push | Whether to go | Medium | M | Existing alerts Worker (free plan: 15 sends a run, 1,000 KV writes a day) | No | Needs a location the user gives; keep it to saved spots first |
| 9 | Blue-green algae reports | Whether to get in | Medium in summer | S once licensed | UKCEH Bloomin' Algae publishes a CSV (5,389 rows for 2026, lat/lon, date, verification status) with no licence stated. The EA hydrology `bga` sensors: 19 live | No | Blocked on a licence email to UKCEH (open question) |
| 10 | Estuary and coastal designated sites, PRF only | Where to go, coastal | Medium | M | EA PRF, OGL | No | SwimSignal abstains; shows the EA's forecast and credits it |
| 11 | Scotland | Where to go | Medium | L | Scottish Water near-real-time overflow API (2,074 assets, OGL, hourly); SEPA KiWIS river levels (394 stage stations, OGL); SEPA bathing waters 90, PRF at some, HTML only. OS Open Rivers already covers GB | No | No annual-return spill history, so the per-overflow calibration layer is missing; pooled model only. Worth a separate plan |
| 12 | Wales | Where to go | Low until a feed exists | L | Dŵr Cymru has a public map since 2024 but its site refused our requests and no API or licence was confirmed; only annual EDM returns are OGL | No | Ask Dŵr Cymru (open question) |
| 13 | Weekend planner | Whether to go | Low | S | Existing | No | The five-day strip already is one; add "Saturday / Sunday" words to the best-day sentence |
| 14 | Accuracy badge per spot | Trust | Low to medium | M | Live verification log | No | Scores are per overflow, not per spot; a per-spot badge would be the mean skill of its contributors, which is honest only with a note |
| 15 | Embeddable widget and public data files for clubs and councils | Reach | Medium | S | Our files, with the credits | No | `spots.json` is already public; a documented `data/` page and a 1-line iframe cost little |
| 16 | Per-spot photo and access notes | Where to go | Medium | L | Photos: Wikimedia Commons (CC BY-SA, per file) or Geograph (CC BY-SA 2.0); access notes must be written | No | Licences are per image and need attribution on the page; defer |
| 17 | Club features (post swim times) | Community | Low for a forecast site | L | Would need accounts and storage | Yes | Not SwimSignal's job; link to clubs |
| 18 | Non-English readers | Access | Low in England; Welsh matters if Wales is covered | M | Translation | No | Welsh with Wales |
| 19 | Offline maps | Where to go | Low | M | Tiles' licence (OSM tiles not for bulk download) | No | The service worker already keeps the shell and last forecast |
| 20 | Apple/Google Wallet pass, Siri Shortcuts, native apps | Convenience | Low | L | Apple Developer Program $99 a year; Wallet passes need an Apple-issued certificate; Siri intents need a native app; Google Play $25 once | No | Not now |
| 21 | Tides for the coast | Whether to get in, coastal | n/a until coastal | M | EA tide gauges (88, OGL, observed only); UKHO Admiralty tidal API (607 stations, 6-day predictions, free tier, terms not confirmed) | No | Only with 10 |

### 3.3 The credibility layer

What exists: a live verification page with the holdout tables, the lead-time tables, the E. coli
validation and a live table that restarted on 29 Sep under the strict coverage rules. As of
the 3 Oct build it scores 19,015 overflow-days (29 Sep to 2 Oct): calibrated Brier 0.0287
against 0.0397 for climatology and 0.0320 for a flat forecast at the period's own spill rate;
AUC 0.826; in-advance forecasts (15,196) Brier 0.0293. The E. coli live table has 24 scored
samples and no skill yet (AUC 0.54), which is what a four-day window at season's end looks
like and should be said that way.

What a sceptical swimmer or a journalist would want, in order of cost:

1. **A methods page in plain words** (one page, not the README): what the level is, what it is
   not, the three validation results in one table each, the known failures (lakes, diffuse
   runoff, over-confidence at the top end), and the correction log that the README already
   keeps. Task 7.
2. **The daily scored CSV published** as a file anyone can download and recompute from. The
   build already writes `verification_live.json`; add the row-level CSV (overflow id, day,
   forecast by lead, observed) to `data/` with the credits.
3. **Side by side with the Environment Agency's PRF** at the inland bathing waters where the
   EA issues one. Same days, same sites, both against the EA's own samples. If SwimSignal is
   level with the EA's forecast at designated sites, its claim to be useful at the undesignated
   ones is credible. If it is worse, say so and show by how much.
4. **An independent review**: a named hydrologist or a Rivers Trust scientist reading the
   method and saying so in public. This needs the three items above to exist first, and an
   email from Ethan (open question).
5. **Pre-registration of next season's test**: write down in April what will be scored in
   October. Costs nothing and is the one thing that separates a forecast from a story.

### 3.4 The sustainability layer

What keeps it free: GitHub Actions is free for public repositories on standard runners (6-hour
job limit, 10 GB cache); GitHub Pages allows 1 GB and a soft 100 GB a month; Open-Meteo's free
tier is 10,000 calls a day and 300,000 a month for non-commercial use, which a no-revenue,
no-ads open-source site meets under their wording (*inferred* from the terms, not confirmed with
them); Cloudflare Workers free gives 100,000 requests a day and Workers KV 1,000 writes a day.

| Feature | Breaks free? | Cheapest step up |
|---|---|---|
| Click anywhere, static (tasks 3, 4) | No. 33 rain requests a build; a few MB of files | None |
| All-overflow daily probabilities | No | None |
| Push alerts at scale | Yes at about 1,000 subscription changes a day (KV writes) or 450 sends an hour | Workers Paid, $5 a month minimum |
| FastAPI deployed (true any-point with gauges) | Yes. Needs about 2 GB of memory for the network | A 2 GB VM. Fly's 256 MB machine ($2.19 a month) cannot hold it; Fly's 2 GB price and Hetzner's current prices were not confirmed today. Oracle's Always Free tier gives 2 OCPU and 12 GB but reclaims idle machines |
| A commercial tier, ads or paid alerts | Yes: Open-Meteo moves to a paid plan ($29 a month Standard per their 2023 blog; the pricing page prints no prices) and the Met Office rainfall's CC BY-SA attribution still applies | Open-Meteo Standard |
| Native app, Wallet pass | $99 a year Apple, $25 once Google | n/a |

The cheapest useful step up, if one is ever needed, is Workers Paid at $5 a month for alerts.
Nothing in the recommended sequence needs it.

## 4. PR-sized tasks for agents

Each task is self-contained. Branch from `main`, open a PR against `main`, do not stack.
Run `uv run pytest -q` and `node --test tests/*.test.cjs` before opening. Follow
`docs/DESIGN.md` for any words or layout. Never push to `main` or dispatch `site.yml` to test;
the build can be run locally with `DIPCAST_STATE` pointing at a copy of the `state` release
and `data/processed/river_network.pkl` from the `data-v1` release (both `gh release download`).

### Task 1: a duplicated overflow id must not fail a spot's forecast

In the 3 Oct 2026 12:06 UTC build, 4 of 89 spots failed with "Index contains duplicate
entries, cannot reshape" (Nidd at the Lido Knaresborough, River Teme in Ludlow, River Severn at
Ironbridge, River Severn in Shrewsbury). `spill_probabilities` in `src/dipcast/model/forecast.py`
pivots the site-day frame on `site_id`; it fails when the overflow table passed in has one
`site_id` twice. The live snapshot can carry such a row: `state/live_latest.parquet` from the
3 Oct 02:59 poll holds Anglian Water's AWS00528 twice, identical. The ids that duplicated at
12:06 were not captured and are *inferred* to be Severn Trent and Yorkshire Water rows.

Do: in `src/dipcast/overflows.py` (`build_overflows`, or where the live snapshot is joined) drop
duplicate `site_id` rows keeping the most recent `fetched_at`, and log a warning naming them.
Also make `spill_probabilities` robust: de-duplicate `ov` on `site_id` before the pivot, so a
duplicate can never fail a forecast. Add a test in `tests/test_core.py` that builds a small
overflow frame with one id repeated and asserts `forecast_point`-level code returns a result.
Add a `duplicate_overflow_ids` count to `build_health` in `scripts/build_site.py` so the
Actions annotation shows it.

Accept when: the test fails before the change and passes after; `uv run pytest -q` is green;
a local build with the 3 Oct state produces 89 results with `forecast_failed: 0`.

### Task 2: a plain level for spots the model cannot help

19 of 89 spots show "No monitored overflows upstream" or "Not covered by the forecast" (see
`src/dipcast/site/levels.js`, `NO_OVERFLOWS`, `NOT_COVERED`, `COVER`, and the headline code
around line 1001 of `src/dipcast/site/index.html`). Replace with:

- Level word: "No sewage risk from monitored overflows" where the trace found none within
  60 km; "No river connection: overflows cannot reach this lake" for an isolated lake
  (`error` text begins "An isolated lake"). Keep the clear colour for the first and the
  unknown colour for the second.
- One line beneath, kept in view: "Other risks apply: algae, wildlife, runoff and bathers.
  Check the signs at the water." Keep the EA rating sentence, the algae check and the river
  level where present. Show rain in the last 48 hours (`days[0].rain_48h_mm`) as a plain
  sentence, "12 mm of rain in the last two days", since rain was the only signal that
  correlated with E. coli on lakes.
- Do not show the E. coli estimate on these spots (it is already withheld on lakes; make sure
  a river spot with no overflows upstream does not show one either, since the model was fitted
  on sites with overflows upstream).
- Saved cards, the map tooltip and the list row use the same words. Add the new words to
  `docs/DESIGN.md` under Words, and to the About page's list of levels.

Accept when: `node --test tests/site_planner.test.cjs` covers a spot with `days` but no
`contributors`, and an isolated lake; a local build renders both (Henleaze Lake and Buttermere)
with the new words; no page shows a bare "No overflows upstream" anywhere (grep the built
`site/`).

### Task 3: publish the data for click-anywhere forecasts

New script `scripts/build_any_point.py`, run by `scripts/build_site.py` after the spot
forecasts. It writes to `site/data/anypoint/`:

1. `overflow_days.json`: for every overflow in `load_overflows()` (14,632), the spill
   probability for each of the five days from `spill_probabilities` (the same call the spots
   use), the `ok` mask, and the live weight the site uses for "right now". Keep it under
   1 MB: ids as the array index into a separate `overflow_ids.json`, probabilities rounded to
   three decimals. Include the `credits` object (`data_credits`).
2. `links/<tile>.json`: once per network release, for every link with at least one upstream
   overflow within `config.MAX_UPSTREAM_KM` (measured today: 18% of inland links, mean 2.5
   overflows), the list of (overflow index, distance_m, dilution) from `upstream_overflows`
   with `river_velocity(None)`. Tile by 0.25° squares. Cache the result in the `state`
   directory keyed by the network file's hash so it is not recomputed every build.
3. `link_index/<tile>.bin`: each link's id, midpoint and form, as packed Float32/Int32 arrays,
   so the page can snap a click to the nearest link with no server.
4. Lakes: for each WFD lake polygon (`data/processed/lakes.parquet`), its inlets' upstream
   lists and the polygon's area, in `lakes.json`.

Measure and log the total size and the build time added. Add the credits to the terms page's
list if any new source appears (none expected). Add a test that builds the tiles for a tiny
synthetic network and checks a link's list matches `upstream_overflows`.

Accept when: a local build writes the files; the total under `site/data/anypoint/` is under
15 MB; the build step takes under 60 s longer than before on the same machine; `pytest` is
green.

### Task 4: click anywhere on the static page

Depends on task 3 being merged. In `src/dipcast/site/index.html` and a new
`src/dipcast/site/anypoint.js` (add it to `SHELL_SOURCES` and `VERSIONED_SCRIPTS` in
`scripts/build_site.py`): a click on the map away from a listed spot fetches the tile for that
square, snaps to the nearest link within 1.5 km (the API's rule), loads the link's upstream
list and `overflow_days.json`, and computes the five-day exposure index exactly as
`src/dipcast/model/transport.py` does: `decay = 10^(-travel_h / 30)`, travel at 0.5 m/s,
dilution from the table, `risk = 1 − ∏(1 − p_i·w_i)`, then the lead calibration knots from
`verification.json` (`lead_calibration.leads`). Lake clicks use `lakes.json` and the straight-line
distance at 0.05 m/s with the area dilution `1 + area/5`. Show the result in the same card as
a listed spot, titled by the watercourse name from the link index and marked "Unlisted point:
not hand-checked", with the same caveats. No E. coli estimate for unlisted points (the model
needs the spot's own rain window, which the page does not have). Offer "Request this as a
spot", which opens the existing issue template with lat/lon filled in.

Add a test (`tests/site_anypoint.test.cjs`) that runs the JavaScript transport arithmetic on a
fixture and matches the Python result for one listed spot to three decimals.

Accept when: clicking the Wharfe between Ilkley and Burley gives a level within one band of
the two listed Wharfe spots; an isolated lake click says so; the service worker caches the
new script; Lighthouse's performance score on the home page does not drop by more than 5.

### Task 5: OpenStreetMap swim spots, reviewed, in a separate ODbL file

Query Overpass (User-Agent naming the project and the repo) for
`leisure=bathing_place`, `leisure=swimming_area`, `sport=swimming` not on a pool, and names
matching swim/bathing place/plunge pool, within `ISO3166-2=GB-ENG`. Today that is 402 distinct
elements; after removing pools, gyms, tidal pools and duplicates within 150 m, about 111, of
which about 96 look inland. Write `scripts/osm_spot_candidates.py` that produces
`data/raw/osm_swim_candidates.csv` with the OSM id, tags, lat/lon, and the nearest network
link's watercourse name and distance (`locate_pin`), and prints the ones within 300 m of an
existing spot (10 today). Then **a human reads every row** and keeps the natural river and lake
spots the public can reach, filling `river` for rivers.

Put the kept rows in a new `spots-osm.csv` with the same columns plus `osm_id`, with ids that
do not overlap `spots.csv` (`osm-` prefix) and no column that joins the two files. The build
reads both files; `placement_check` runs on both; the terms page's credits gain
"Swim spot locations from OpenStreetMap, © OpenStreetMap contributors, ODbL 1.0" with a link to
`https://www.openstreetmap.org/copyright`; `spots.json` carries the same in `credits` and each
OSM spot carries `source: "openstreetmap"`. Add a `LICENSE-DATA.md` note saying `spots-osm.csv`
is ODbL and `spots.csv` is not.

Accept when: the review is in the PR description as a table (kept, dropped, why); every kept
river spot passes `placement_check`; the build adds no warning; the spot count on the home
page updates.

### Task 6: answer spot requests automatically

A workflow `.github/workflows/spot-request.yml` on `issues: [opened, edited]` with the
`spot-request` label: parse the template fields, geocode a what3words or text location only if
lat/lon are absent (no key: ask the requester for coordinates instead), run `locate_pin` and
`placement_check` logic from `scripts/build_site.py` against the network (download it from the
`data-v1` release as `site.yml` does), and post one comment: the snapped watercourse, the
distance, how many monitored overflows are upstream, and a ready-to-paste `spots.csv` row.
Give the workflow `issues: write` only. Add a `tests/test_spot_request.py` for the parser.
Update the issue template's intro to say what the bot will answer with.

Accept when: a test issue on a throwaway branch gets a comment within the run; a request with
a bad location gets a polite ask for coordinates, not a crash.

### Task 7: methods page, scored CSV and the EA comparison

Three parts, one PR:

1. `src/dipcast/api/static/methods.html` (and the static route list in `REWRITES`): a
   one-page plain-words method, from the README's Method section, with one table each for the
   holdout, the lead-time and the E. coli validation, the known failures, and the correction
   log. Link it from the Accuracy page and the foot.
2. `site/data/verification_live.csv`: one row per scored overflow-day (overflow id, company,
   day, lead, forecast raw and calibrated, observed), written by the scorer with the credits in
   a header comment. Link it from Accuracy.
3. `scripts/compare_prf.py`: for each of the 38 inland bathing waters, fetch the EA's daily
   risk prediction history (`/doc/bathing-water-quality/stp-risk-prediction.json?predictedOn=`
   per day of the 2026 season; `prfOriginType` says whether the site had a PRF), and score
   "increased" against the EA's own samples over 900, beside SwimSignal's E. coli estimate on
   the same days from the forecast log. Write `data/processed/prf_comparison.json` and a table
   on Accuracy. Count and report how many inland sites had a PRF at all (unknown today).
   Note the gateway refused GitHub's runners from 28 Sep; run the fetch locally and commit the
   result if so.

Accept when: the methods page validates and passes `tests/test_site_pages.py`; the CSV has the
same row count as `n_scored` in `verification.json`; the comparison table says plainly which
forecast did better and on how many samples.

### Task 8: "too high to swim" from the level gauge and flood alerts

`attach_river_levels` in `scripts/build_site.py` already gives 75 spots the nearest gauge's
level and `index` (0 at typical low, 1 at typical high). Add: a `flow_state` word per spot,
"high" when `index > 1.0` on the same river, "rising fast" when the last six hours rose more
than 0.2 of the typical range (fetch the readings with `_limit=24` from the flood-monitoring
API), and the EA flood alerts within 10 km from
`https://environment.data.gov.uk/flood-monitoring/id/floods?lat=&long=&dist=10` (severity
1 to 4, OGL, already credited). On the page: a sentence under the headline, kept in view,
"River high: the gauge at Addingham is above its usual range" or "Flood alert in force here
(Environment Agency)", with the level section's existing "Not part of the pollution level"
note. Do not change the level; a high river is a different hazard and the words must say so.

Accept when: `tests/test_flows.py` covers the word rules; a spot with a live flood alert in a
fixture shows the sentence; no spot shows "River high" from a stale reading.

### Task 9: water temperature where the EA has a live sensor

The EA Hydrology API (OGL) has 2,012 temperature measures but most are finished sonde
deployments; 102 reported readings in the week to 3 Oct 2026. Add `attach_water_temperature`
beside `attach_river_levels`: fetch
`https://environment.data.gov.uk/hydrology/data/readings.json?observedProperty=temperature&mineq-date=<7 days ago>`
once per build, keep the latest reading per station, and give each spot the nearest station
within 15 km on the same river (reuse `same_river`), with its distance and the reading's age.
Show "Water 11 °C at Ilkley gauge, 3 km upstream, 2 h ago"; show nothing where there is no
station, never an estimate. Add the Hydrology API to the credits if it is not already listed
(the README lists it under EA flood-monitoring; check the terms page).

Accept when: a test with a fixture of readings picks the right station and rejects one 40 km
away; the build log says how many spots got one; the page shows it only with distance and age.

### Task 10: public data page and an embeddable card

A `data.html` page listing every file under `site/data/` with its schema, licence and credits
(much of this is in `data_credits`), and an `embed.html?spot=<id>` that renders one spot's
five-day strip and level with the caveat line and a link back, for clubs and councils to
iframe. Document both on About.

Accept when: `tests/test_site_pages.py` covers both pages; the embed renders at 320 px wide;
the terms page says embedding is allowed with the credits intact.

## 5. Open questions only Ethan can answer

1. **Ask the Outdoor Swimming Society?** A credited link exchange costs an email to
   hello@outdoorswimmingsociety.com; their map is down and their terms reserve all rights, so
   nothing can be used without it. Yes or no, and if yes, what SwimSignal offers them.
2. **Ask UKCEH about Bloomin' Algae?** The CSV is public with no licence line. Reports could
   feed a "blooms reported nearby" sentence in summer. Who sends the email and under what
   framing (research use, credit, link back).
3. **Ask Dŵr Cymru for a live feed?** Wales stays history-only without one. Their site refused
   automated requests, so this is a person-to-person ask.
4. **Scotland: in or out for 2027?** The data exists under OGL (Scottish Water live feed,
   SEPA levels) and the network already covers Scotland, but there is no spill history for the
   calibration layer and no validation data. It is a season's work.
5. **Who reviews the method?** Item 4 of 3.3 needs a named person. A Rivers Trust scientist,
   a university hydrologist, or the EA's bathing-water team.
6. **Accept ODbL for the OSM spots in a separate file**, or skip OSM altogether and rely on
   click-anywhere plus requests?
7. **Any budget at all?** The plan needs none. If $5 a month is acceptable, alerts can scale
   (Workers Paid). If about $10 to $20 a month is acceptable, the API could be deployed on a
   2 GB machine (price not confirmed) and clicks would get the gauge-scaled velocity and the
   E. coli estimate; the static path makes that optional.
8. **Season timing.** The E. coli live table cannot be read until next May. Should the winter
   work be the trust layer (tasks 7 and 10) so that the May results land on a page people can
   already find?

## 6. Sources and how each number was obtained

- spots.csv counts: `python3` over the file; kinds and sources from its columns.
- Live build: `https://swimsignal.co.uk/data/spots.json` generated 2026-10-03T13:07 (the
  `build` object: `forecast_failed: 4`, `no_forecast_possible: 2`, `river_levels: 75`,
  `weather: 85`), and `gh run view 37121579896 --log` for the step times and the four errors.
- Overpass: `https://overpass-api.de/api/interpreter`, area `ISO3166-2=GB-ENG`, queries for
  `leisure=bathing_place` (21), `leisure=swimming_area` (57), `sport=swimming` without a pool
  tag (182), names matching swim/bathing/plunge (191), `natural=beach` count (2,709). The
  filter and 150 m de-duplication are a script in this session's scratchpad; the kept list is
  reproducible from the queries and the rules in task 5.
- ODbL and OSMF guidelines: opendatacommons.org/licenses/odbl/1-0 sections 4.4 and 4.5;
  osmfoundation.org Collective Database Guideline and Horizontal Map Layers guideline;
  openstreetmap.org/copyright. Taginfo: `leisure=bathing_place` 5,879 uses worldwide.
- OSS: outdoorswimmingsociety.com/wild-swim-map returns 404; terms at
  outdoorswimmingsociety.com/terms-conditions. SAS: datahq.sas.org.uk ("850 UK locations").
  Rivers Trust EDM layer metadata on arcgis.com (OGL). wildswimmap.co.uk (649 spots).
- EA bathing waters: `environment.data.gov.uk/doc/bathing-water.json?_pageSize=200&_page=N`,
  464 rows typed Coastal 373, Transitional 53, River 20, Lake 18; `stp-risk-prediction.json?
  predictedOn=2026-09-15`: 175 PRF_PROVIDED, 276 NON_PRF_SITE. Licence line on
  environment.data.gov.uk/bwq. NRW: `environment.data.gov.uk/wales/bathing-waters` (114).
  SEPA: beta.sepa.scot news, 90 bathing waters.
- EA flood-monitoring and Hydrology APIs: reference pages (OGL); `id/measures` counts
  (temperature 2,012 measures); `data/readings.json?observedProperty=temperature&mineq-date=
  2026-09-26` (102 measures with data); `id/floods?lat&long&dist` tested; tide gauges 88.
- Bloomin' Algae: bloominalgae.ceh.ac.uk/data/BA_records.csv (5,389 rows), no licence stated.
- Scottish Water: api.scottishwater.co.uk/overflow-event-monitoring/v1/near-real-time (2,074
  assets; data.gov.uk entry says OGL). SEPA KiWIS: timeseriesdoc.sepa.org.uk (OGL).
- Network and overflows: `river_network.pkl` from the `data-v1` release (113,308,879 bytes),
  loaded with `dipcast.model.forecast._net` (38 s); `load_overflows` with the `state` release
  of 3 Oct 02:59 (14,632 rows, one duplicate id); 300 random `inlandRiver` links sampled with
  `locate_pin` and `upstream_overflows`; `cells_for_sites` over all overflows (1,645).
- Verification: `https://swimsignal.co.uk/data/verification.json`, `live.overall`,
  `live.in_advance`, `live.by_lead`, `live.ecoli_live`.
- Prices and limits, read 3 Oct 2026: docs.github.com (Actions billing, limits, Pages limits);
  developers.cloudflare.com (Workers limits and pricing, KV, R2, D1, Pages); open-meteo.com/en/
  pricing and /en/terms (prices themselves from Open-Meteo's June 2023 blog, so possibly
  stale); fly.io/docs/about/pricing; developer.apple.com (programs, Wallet); Google Play
  console help. Hetzner's pages rate-limited the check, so no Hetzner price is cited.
