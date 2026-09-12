# dipcast

Probabilistic sewage-pollution risk for river and lake swim spots in England.

Click a point on a river or lake. dipcast traces the river network upstream,
finds every monitored storm overflow whose water reaches that point, and combines
(a) what those overflows are doing right now, from the water companies' live
feeds, with (b) how likely each is to spill over the coming days, from a model
of rainfall and each overflow's history, then attenuates every spill for travel
time, bacterial die-off and dilution before it reaches the swimmer.

It is a forecast, not a water-quality measurement. A low reading is not a
guarantee of clean water.

## What exists already, and what this adds

Live sewage maps exist (the National Storm Overflow Hub, Surfers Against Sewage,
WaterWatch, The Rivers Trust) and bathing-water risk forecasts exist for the ~950
designated bathing waters (Islandswim, SAS). Inland river and lake spots, where
most freshwater swimming happens, are mostly not designated bathing waters and
had no forecast. dipcast covers any point on the network. Its distinguishing
part is the transport step: an overflow 2 km upstream on the same river and one
40 km up a tributary are not treated the same.

## Data

| Source | What | Access |
|---|---|---|
| Water company live feeds (9 companies, ArcGIS) | Current status of ~14,200 overflows, latest event start/end | Open, no key |
| United Utilities EDM event history 2023-2025 | 743,735 discharge events with start/end | Open, no key |
| EA storm overflow annual returns 2021-2025 | Spill counts and hours per overflow per year, WFD waterbody, for all 10 companies | Open, no key |
| Open-Meteo | Hourly rainfall: ERA5-Land archive for training, model forecast for prediction | Open, no key |
| OS Open Rivers | 193,040 directed watercourse links incl. lake traversals, BNG | Open Government Licence |
| EA WFD Lake Water Bodies Cycle 3 | 564 lake polygons (lakes over 50 ha, 5 ha in protected areas), names and areas | Open, no key |
| EA flood-monitoring API | Near-real-time river levels and typical ranges | Open, no key |

Dwr Cymru (Wales) publishes no live feed to ArcGIS; its 128 overflows appear with
annual spill history only and no "right now" status. Every English company is live.

## Method

**Spill model.** One row per overflow per day. Target: any discharge that day.
Features: rainfall that day and the previous two, 3/7/30-day totals, an
antecedent precipitation index, peak 1/3/6-hour intensities, season, and the
overflow's long-term spill count and hours from the EA annual return of the
previous year. Layer 1 is a gradient-boosted classifier with monotone constraints
(more rain can never lower the probability). Layer 2 is a per-overflow
calibration: the Gamma-Poisson posterior mean of observed/expected spill-days,
so overflows with lots of history get their own level and the rest stay near
the pooled prediction. Trained on United Utilities 2023-2024, verified on 2025,
See `scripts/train.py`; the table below is `data/processed/verification_2025.csv`.

Held-out 2025, 826,725 overflow-days across 2,241 United Utilities overflows,
base rate 7.45% of days with a discharge:

| Model | Brier | Log loss | AUC | Brier skill vs climatology |
|---|---|---|---|---|
| dipcast (pooled + site calibration) | 0.0447 | 0.154 | 0.929 | 0.33 |
| pooled only | 0.0452 | 0.156 | 0.927 | 0.33 |
| per-site climatology | 0.0669 | 0.246 | 0.768 | 0.00 |
| naive rule: >10 mm in 48 h | 0.0621 | 0.224 | 0.758 | 0.07 |

Reliability is good below 30% forecast probability and mildly overconfident
above 70% (forecast 0.85 verifies at 0.78). Dropping the negative subsampling
does not change this (Brier 0.0449), and recency-weighting the site
calibration improves it only slightly (0.0445), so the residual is a year
effect: 2025 had a 7.45% spill-day rate against 10.1% in 2023-24 for the same
overflows, and a model fitted on the earlier years over-predicts it. On wet days (more than 10 mm in
48 h, 22% of overflow-days spill) the Brier score is 0.112 against 0.157 for
climatology; on dry days 0.023 against 0.038.

**Transport.** For each upstream overflow: distance along the network (plus a
straight-line lake crossing for lake spots), travel time from a reach velocity
scaled by the nearest gauge's level index (0.05 m/s across lakes), first-order
die-off with T90 = 30 h, and dilution as the ratio of upstream network length
at the outfall to that at the spot. The product is the probability that a spill
there affects the spot. Risk = 1 - prod(1 - p_i w_i).

**Lakes.** A click inside or within 150 m of a WFD lake polygon is treated as
that lake. The lake's centreline links (OS Open Rivers `form = lake`) inside the
polygon define its outlet; every overflow upstream of the outlet contributes,
its water routed along the network to where it enters the lake and then
straight-line to the click. Lake dilution divides the weight by
1 + area / 5 km², so Windermere's north basin (8.7 km²) cuts a shoreline spill's
weight to about a third. Windermere is two WFD basins and is treated as such.
Lakes not in the WFD set (small tarns) fall back to the centreline heuristic.

**Right now.** Same weights applied to live status: discharging = 1, finished
within 48 h decays with time since the event ended.

**Skill with real forecasts.** The table above uses reanalysis rainfall, so it
excludes weather-forecast error. `scripts/verify_leads.py` repeats the 2025
test with Open-Meteo's archived forecasts by lead time, using the same
held-out model (trained 2023-2024). Lead 0 is the latest run for the day
(what "today" uses); lead k is the forecast issued k days earlier.

| Rainfall source | Brier | AUC | Brier skill vs climatology |
|---|---|---|---|
| reanalysis (ERA5-Land) | 0.0447 | 0.929 | 0.33 |
| forecast, lead 0 (today) | 0.0429 | 0.933 | 0.36 |
| forecast, lead 1 (tomorrow) | 0.0469 | 0.913 | 0.30 |
| forecast, lead 2 | 0.0484 | 0.913 | 0.28 |
| forecast, lead 3 | 0.0504 | 0.902 | 0.25 |
| forecast, lead 4 | 0.0536 | 0.892 | 0.20 |

Today's forecast rain beats reanalysis, presumably because the forecast model
runs at about 2 km against ERA5-Land's 10 km. Skill decays with lead but stays
ahead of the rain rule (0.0621) four days out. The daily rainfall itself has a
mean absolute error of 2.0 mm at lead 0 rising to 3.1 mm at lead 4, and catches
67% of days over 10 mm at lead 0 against 45% at lead 4. Treating forecast rain
as certain makes longer leads over-confident (at lead 2 a 75% forecast verifies
at 65%), so a two-parameter Platt scaling is fitted per lead and applied in the
API; `data/processed/lead_calibration.json` holds the parameters. After
calibration the lead-2 75% bin verifies at 72% and lead-4 Brier improves from
0.0536 to 0.0524; the parameters are fitted on the same 2025 data, so treat
those two figures as slightly optimistic.

## Known limits

- The lead-time test approximates the features: the target day and the two
  before it use lead-appropriate forecasts; the 7-day, 30-day and antecedent
  windows use reanalysis, since on the issue day they are almost all observed.
- Snapping outfalls to the network: 85% sit within 750 m of a link ("high"
  confidence). A second pass to 1.5 km recovers 9% more, preferring a link whose
  name shares a distinctive word with the recorded receiving watercourse
  ("medium", 501 outfalls) and otherwise taking the nearest ("low", 862, weight
  scaled by 0.7). 475 outfalls to the sea or estuaries are excluded by design and
  335 inland ones (2%) remain more than 1.5 km from any link. Confidence is
  reported per contributor.
- Dilution uses network length as a proxy for flow. Lake volume is not modelled;
  lake crossing uses a fixed slow advection speed.
- Daily resolution. Sub-daily timing of a plume is not resolved.
- Annual returns before 2024 carry old or no overflow IDs; `dipcast/ids.py` resolves 93% of rows (100% of UU 2023, 92% of UU 2021-22) via the Hub lookup, then site name and grid reference.
- Only United Utilities publishes event-level history on ArcGIS, so the site
  calibration layer covers their overflows. Others use the pooled model with
  annual-return covariates, and the live poller accumulates their history.

## Status (12 Sep 2026)

Working end to end on a local machine: click anywhere on a river or lake in
England and get a "right now" risk from live status plus a five-day forecast,
with the contributing overflows listed and drawn on the map. Verified on a
held-out year (table above). Unit tests cover the label exploder, rainfall
features and the risk combination.

Done since v1: Southern Water live feed (all nine English companies now live);
second-pass outfall snapping (85% to 94%); shareable URLs; WFD lake polygons with a lake-size dilution term; recency
weighting in the site calibration; production refit on all years; verification
against archived forecasts by lead time (below); `Dockerfile`; a launchd plist
in `deploy/` for the 20-minute live refresh (not installed automatically).

Not done yet, in the order I would do them:

1. A year-level calibration: the residual top-end overconfidence is a shift
   between years, so fit a single temperature or isotonic map on the most
   recent year's out-of-sample predictions each time the model is refit.
2. Install the refresh schedule and let live history accumulate for the seven
   companies without event feeds, then fit their site calibration.
3. Per-lake residence time (needs volume; WFD gives area only).
4. Deploy the container somewhere public.
5. Dwr Cymru live status (no ArcGIS feed; would need their own map's API).

## Run

```bash
uv sync
uv run python -m dipcast.ingest.annual_returns
uv run python -m dipcast.ingest.live
uv run python -m dipcast.ingest.edm_events
uv run python scripts/fetch_rain_archive.py
uv run python -m dipcast.overflows
uv run python scripts/train.py 2025
uv run uvicorn dipcast.api.app:app --port 8000
```

Open http://localhost:8000. Live status refreshes in-process every
`DIPCAST_REFRESH_MINUTES` (set it, e.g. `DIPCAST_REFRESH_MINUTES=20`); with it
unset, run `scripts/refresh.py` on a schedule and call `POST /api/reload`
(`deploy/com.ethanbuckley.dipcast.refresh.plist` does this on macOS). Mutable
files (live polls, the overflow table, the forecast log, live scores) go to
`DIPCAST_STATE` if set, else `data/processed`. All raw pulls are cached under
`data/cache/` so re-running the ingestion is cheap. `scripts/verify_leads.py
2025` reproduces the lead-time table.

Every forecast is logged (coordinates, time, values; nothing about the user)
and scored once its days have passed, using the accumulated live polls. The
result is public at `/verification`, alongside the offline tests. `/terms` and
`/privacy` hold the plain-English terms and privacy notice.

## Deploy

The app is one container plus a small volume for mutable state. `fly.toml` is
set up for Fly.io in London; any host that runs a container works the same way.
The river network needs about 1 GB of RAM at runtime, so the config asks for a
2 GB machine (roughly £10 a month at 2026 prices; check Fly's pricing page).

```bash
brew install flyctl                 # or curl -L https://fly.io/install.sh | sh
fly auth signup                     # or fly auth login
fly launch --no-deploy --copy-config --name dipcast --region lhr
fly volumes create dipcast_state --region lhr --size 1
fly deploy                          # builds the Dockerfile remotely, ~10 min first time
fly open /api/health
```

You will know it worked when `/api/health` returns `"refresh_minutes": 20` and
the map loads at `https://dipcast.fly.dev`. The mistake to avoid is deploying
before `data/processed` exists locally: the Dockerfile copies it into the
image, and an empty directory produces a container that starts and then fails
every forecast.

Custom domain and HTTPS: buy a domain at any registrar, then

```bash
fly certs add dipcast.example.com
```

and create the DNS records `fly certs show` asks for (an A and an AAAA record,
or a CNAME to `dipcast.fly.dev`). Fly issues and renews the certificate. The
`.fly.dev` address is HTTPS already, so a domain is cosmetic.
