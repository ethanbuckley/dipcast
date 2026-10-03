# SwimSignal

Probabilistic sewage-pollution risk for river and lake swim spots in England.
The code and the Python package (`dipcast`) keep the working name dipcast.

Live site: https://swimsignal.co.uk/ (forecasts for 105 named
spots, rebuilt several times a day by a scheduled GitHub Actions job; free to
run, never sleeps). Source: https://github.com/ethanbuckley/swimsignal (MIT).
Every forecast issued is scored later and published on the site's
verification page.

Click a point on a river or lake. SwimSignal traces the river network upstream,
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
had no forecast. SwimSignal covers any point on the network. Its distinguishing
part is the transport step: an overflow 2 km upstream on the same river and one
40 km up a tributary are not treated the same.

## Data

| Source | What | Licence (all open, no key) |
|---|---|---|
| Water company live feeds (9 companies, ArcGIS, via the National Storm Overflow Hub) | Current status of ~14,200 overflows, latest event start/end | CC BY 4.0, per company |
| United Utilities EDM event history 2023-2025 (via Stream) | 743,735 discharge events with start/end; training only | CC BY 4.0 |
| Stream ID lookup | Company ids to EA permit ids (`id_lookup.parquet`) | CC BY 4.0 |
| EA storm overflow annual returns 2021-2025 | Spill counts and hours per overflow per year, WFD waterbody, for all 10 companies | OGL v3 |
| EA bathing water quality (bwq) | Classifications (`data/raw/bathing_water_classifications.json`) and in-season samples | OGL v3 |
| Open-Meteo | Hourly rainfall: ERA5-Land archive for training, model forecast for prediction | CC BY 4.0; the forecast for England is Met Office data, CC BY-SA 4.0 |
| OS Open Rivers | 193,040 directed watercourse links incl. lake traversals, BNG | OGL v3 |
| EA WFD Lake Water Bodies Cycle 3 | 564 lake polygons (lakes over 50 ha, 5 ha in protected areas), names and areas | OGL v3 |
| EA flood-monitoring API | Near-real-time river levels and typical ranges; the API version only | OGL v3 |
| EA Hydrology API | Water temperature from water-quality sensors that reported in the last day, beside the forecast | OGL v3 |
| EA Water Quality Archive (Water Quality Explorer) | E. coli results and the sampler's visual algae check at the 38 inland bathing waters | OGL v3 |
| OpenStreetMap (Overpass API) | 16 river and lake swim spots chosen one by one from 115 candidates (`spots-osm.csv`) | ODbL 1.0, © OpenStreetMap contributors |

The notices each provider asks for are on the site's terms page ("Data sources
and credits"), and `data/spots.json`, `data/overflows.geojson` and
`data/verification.json` carry them in a `credits` field (`data_credits` in
`scripts/build_site.py`); `data/verification_live.csv` carries them in comment
lines at its top. The MIT licence covers the code, not the data.

`LICENSE-DATA.md` says which files are under the ODbL (`spots-osm.csv` and the
candidate list it came from) and why `spots.csv` must never take OpenStreetMap rows.

The JSON files under `data/anypoint/` also carry the credits; each square's
link list links to them.

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
| SwimSignal (pooled + site calibration) | 0.0445 | 0.153 | 0.930 | 0.33 |
| pooled only | 0.0452 | 0.155 | 0.927 | 0.33 |
| per-site climatology | 0.0669 | 0.245 | 0.768 | 0.00 |
| naive rule: >10 mm in 48 h | 0.0621 | 0.224 | 0.758 | 0.07 |

Reliability is good below 30% forecast probability and mildly overconfident
above 70% (forecast 0.85 verifies at 0.78). The table is the CSV written on
13 Sep 2026; the two comparisons that follow were measured on the first run of
12 Sep, when the full model scored 0.0447. Dropping the negative subsampling
does not change this (Brier 0.0449), and recency-weighting the site
calibration improves it only slightly (0.0445), so the residual is a year
effect: 2025 had a 7.45% spill-day rate against 10.1% in 2023-24 for the same
overflows, and a model fitted on the earlier years over-predicts it. On wet days (more than 10 mm in
48 h, 22% of overflow-days spill) the Brier score is 0.112 against 0.157 for
climatology; on dry days 0.023 against 0.038.

**Transport.** For each upstream overflow: distance along the network (plus a
straight-line lake crossing for lake spots), travel time at a fixed reach
velocity of 0.5 m/s (0.05 m/s across lakes), first-order
die-off with T90 = 30 h, and dilution as the ratio of upstream network length
at the outfall to that at the spot. The product is the probability that a spill
there affects the spot. Risk = 1 - prod(1 - p_i w_i). The site runs
`forecast_point(..., gauge=False)`, so the river level shown beside a forecast
does not change it; the click-anywhere API (`gauge=True`) scales the velocity by
the nearest EA gauge's level index, 0.3 m/s at typical low to 1.0 m/s at typical
high (`river_velocity`).

**Lakes.** A click inside or within 150 m of a WFD lake polygon is treated as
that lake. The lake's centreline links (OS Open Rivers `form = lake`) inside the
polygon define its outlet; every overflow upstream of the outlet contributes,
its water routed along the network to where it enters the lake and then
straight-line to the click. Lake dilution divides the weight by
1 + area / 5 km², so Windermere's north basin (8.7 km²) cuts a shoreline spill's
weight to about a third. Windermere is two WFD basins and is treated as such.
Lakes not in the WFD set (small tarns) fall back to the centreline heuristic.

**Spot placement.** A clicked point goes to the nearest link within 1.5 km. A
spot in `spots.csv` or `spots-osm.csv` also names its river (the `river` column, blank for lakes),
and goes to the nearest link carrying that name within 1 km, even when a link
of another name is nearer. OS Open Rivers' alternative name counts, and Welsh
names are read as English (`network/names.py`: Afon Gwy is the Wye, Afon Hafren
the Severn, Afon Tefeidiad the Teme). Before this, on 2 Oct 2026, six river
spots were traced up a side beck or a lake and showed no overflows upstream;
Crook o' Lune, for one, sat 262 m from Escow Beck and 672 m from the Lune. With
no such link the nearest is kept and the build logs a warning. Each build then
checks every river spot and adds one line to `build.warnings` in `spots.json`
listing any whose snapped watercourse shares no word with its river, that sits
more than 250 m from it, that has no `river`, that was placed as a lake or an
isolated lake, or that snapped to a canal (`build_site.placement_check`).
Qualifiers such as "Great", "West" and "and" do not count as shared words, so
the Great Ouse does not match Great Agill Beck. A spot moved
from a mapped side channel to the main one (`adopted_main_channel`) is judged
by name only, since its distance is the side channel's offset.

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
at 65%), and the model is also over-confident at the top end even with today's
rain (an 85% forecast verifies at 82%, a 93% one at 86%), a shift between years
that a two-parameter Platt scaling cannot remove. So an isotonic map (monotone,
piecewise, about 50 knots) is fitted per lead on the held-out year's forecasts
and applied in the API; `data/processed/lead_calibration.json` holds the knots,
with the Platt parameters kept alongside for comparison. After calibration every
lead-2 bin sits on the diagonal (the 75% bin verifies at 75%, the 82% bin at
82%) and lead-4 Brier improves from 0.0536 to 0.0522. The map is fitted on the
same 2025 data it is scored on, so treat those figures as slightly optimistic;
the live "By lead time" table shows raw and calibrated Brier side by side, which
is the check that the 2025 map still fits later years.

**Validation against measured E. coli.** The Environment Agency publishes weekly
lab samples at 38 inland designated bathing waters (20 rivers, 18 lakes). For
every sample from May 2023 to September 2026 (2,165 samples; 1,750 at the 32
sites with monitored overflows upstream) `scripts/validate_ecoli.py` computes
what SwimSignal would have said for that day from reanalysis rainfall, and
compares it with the naive competitor, rainfall at the site in the previous
48 hours. Results (Spearman rank correlation with log E. coli; AUC for samples
over 900 cfu/100 ml, the inland "sufficient" threshold):

| Predictor | Pooled ρ | Within-site ρ | AUC > 900 |
|---|---|---|---|
| rainfall, previous 48 h at the site | 0.09 | 0.35 | 0.65 |
| SwimSignal spill risk (rain-driven spill model through transport) | 0.57 | 0.29 | 0.80 |

Two different questions hide in that table. *Which sites* are contaminated:
site-mean SwimSignal risk ranks the 32 sites' mean E. coli at ρ = 0.68, and
site-mean rainfall does not (−0.25). The transport layer, the part of SwimSignal
that is new, is what carries this. *Which days* are bad at a given site: on the
raw scale rain in the last 48 hours leads (0.35 against 0.29; on rivers 0.48
against 0.41), but a within-site comparison depends on the scale the site mean
is removed on. On the logit scale, the scale SwimSignal combines contributions on,
spill risk is level with rain (0.35 against 0.35; rivers 0.47 against 0.48).
Leave-one-year-out linear fits on that scale
(`scripts/validate_ecoli_combined.py`) give within-site ρ of 0.35 for rain
alone, 0.36 for spill risk alone, 0.37 for both and 0.39 with season added
(rivers 0.50, 0.51, 0.53, 0.53), so the two carry partly different
information but neither explains most of the day-to-day variation. At the nine
United Utilities sites, where actual spill events are known (601 samples, 542
of them on lakes), routing the real spills through the transport step
correlates with E. coli at only 0.23 within site, and a spill had reached the
spot within the previous 48 hours for 29% of samples: at those lakes,
bacterial spikes are mostly not overflow-driven.

The honest reading: SwimSignal's forecast tells a swimmer how exposed a spot is
and when the overflows above it are likely to spill, and on rivers its
day-to-day signal is as good as recent rainfall; it does not yet capture the
diffuse runoff (farms, roads, urban drainage) that rain washes into rivers
regardless of overflows, and at inland bathing waters neither signal captures
most of the day-to-day variation. The next model should predict E. coli
exceedance directly from both, which these 2,165 samples make possible. Full
tables: `data/processed/ecoli_validation*.json|csv`.

Correction (14 Sep 2026): the first run of this validation (12 Sep) reported a
within-site ρ of 0.23 for SwimSignal. That run fed only the sample days into the
travel-time shift, which assumes consecutive days, so a fifth of each
overflow's contribution landed on the following week's sample; it also applied
the lead-4 rather than lead-0 Platt calibration. The hindcast now runs on a
continuous daily grid and the figures above are from the corrected run. The
observed-spill result was computed differently and did not change.

**Would forecast-led sampling catch more?** A question for anyone who pays for
water samples: if you could only afford half your sampling days, would choosing
them from the rain forecast catch more of the failures? `scripts/sampling_plan_test.py`
ranks each site's sampled days by rain in the previous 48 h and keeps the wettest
half. On rivers (907 sampled days in 2024-26, 196 exceedances of 900) that keeps
76% of the exceedances with rain as it fell, 75% with the forecast issued that
day, 70% with the forecast issued the day before, and 69% with the forecast
issued two or four days earlier; a fixed schedule keeps 50%. A "sample only if
more than 5 mm is forecast" rule, decided two days ahead, keeps a quarter of the
days and half the exceedances (43% exceedance rate on the days it picks, 14% on
those it skips). Most of the skill is lost between same-day and one-day-ahead
decisions, not after, so a plan made four days out is about as good as one made
the day before. It is the rain forecast doing this work, not the transport
layer; on lakes (16 exceedances) there is nothing to plan around. Archived
forecasts by lead come from `scripts/fetch_rain_leads_bathing.py`; results in
`data/processed/sampling_plan_test.json`. The test can only choose among days the
EA happened to sample, so it measures ranking skill, not the value of sampling on
days nobody did.

Correction (28 Sep 2026): the first run (15 Sep) summed each 48 h window with
pandas, which reads an all-missing window as 0 mm, and Open-Meteo's archive has
no lead 1-4 rain for 2023, so those samples counted as dry at every lead but the
same day. It now uses the production window rule (`ecoli.rain_windows`: 90% of
the hours must carry a value) and compares every lead on the same 2024-26
samples. The river figures moved by one to two points (lead 2 from 68% to 69%;
the same-day figure stayed at 75%); the conclusions did not change.

**E. coli exceedance model.** The map's "E. coli > 900" column: the estimated
probability that a midday sample exceeds 900 cfu/100 ml. `scripts/train_ecoli.py`
fits a logistic regression from things SwimSignal can compute anywhere: rain at the
spot in the previous 48 and 24 h, SwimSignal's overflow exposure for the day, lake or
river, and season. The rain is Open-Meteo's archived lead-0 forecast, the same
source the map uses for "today". The competitor that matters is rain alone: the
question is whether the overflow exposure adds anything a rain gauge would not.

Rebuilt on 16-17 Sep 2026 after the rain-window fixes: 1,548 samples at 32
bathing waters, 2024-2026 (the 202 samples from 2023 are gone, because
Open-Meteo's previous-runs archive has no lead-1 to lead-4 rain for 2023 and the
old code had been summing those nulls to 0 mm and scoring them as dry). Three
tests, each scored on data the model never saw:

| Test | Model | Brier | AUC | Rivers: Brier / AUC | Lakes: Brier / AUC |
|---|---|---|---|---|---|
| leave one year out | climatology by type | 0.116 | 0.62 | 0.181 / 0.35 | 0.025 / 0.38 |
| | rain only | 0.093 | 0.80 | 0.142 / 0.71 | 0.025 / 0.35 |
| | rain + season | 0.094 | 0.80 | 0.143 / 0.73 | 0.025 / 0.37 |
| | spill exposure only | 0.097 | 0.80 | 0.147 / 0.72 | 0.026 / 0.51 |
| | rain + exposure + season (the map) | 0.091 | 0.82 | 0.138 / 0.75 | 0.025 / 0.47 |
| leave one site out | rain only | 0.092 | 0.79 | 0.140 / 0.72 | 0.025 / 0.06 |
| | rain + exposure + season | 0.090 | 0.81 | 0.136 / 0.76 | 0.026 / 0.18 |
| forward: fit 2024, score 2025-26 | rain only | 0.087 | 0.79 | 0.125 / 0.77 | 0.031 / 0.52 |
| | rain + season | 0.087 | 0.79 | 0.125 / 0.77 | 0.031 / 0.52 |
| | rain + exposure + season | 0.083 | 0.81 | 0.117 / 0.81 | 0.031 / 0.53 |

The forward test uses the overflow exposure from the spill model fitted on
2023-24 only, without the 2025-fitted calibration map, so nothing downstream of
the cut-off saw the test years. The season term on its own adds nothing (rain +
season is level with or slightly worse than rain only), so the gain of the full
model is the exposure term.

**Is the gain over rain alone real?** Cluster bootstraps on the out-of-sample
river predictions under three dependence assumptions: resampling site-weeks
(samples at one site in one week share weather and water body), whole sites
(every week at a site moves together; 20 clusters), and calendar weeks across all
sites (one storm hits many sites at once; 40-62 clusters, the most demanding).
Brier gain in thousandths, 95% intervals, rivers (n = 907; forward n = 617):

| Test | Reference | by site-weeks | by whole sites | by calendar weeks |
|---|---|---|---|---|
| leave one year out | rain only | +4.6 [+0.3, +8.8], P 0.02 | +4.6 [+0.9, +8.7], P 0.01 | +4.6 [−2.0, +11.6], P 0.09 |
| leave one site out | rain only | +3.8 [−0.0, +7.7], P 0.03 | +3.8 [−0.5, +8.2], P 0.04 | +3.8 [−1.1, +9.1], P 0.07 |
| forward in time | rain only | +8.3 [+3.1, +13.2], P 0.001 | +8.3 [+1.8, +15.3], P 0.005 | +8.3 [−0.4, +17.7], P 0.03 |
| leave one year out | rain + season | +5.6 [+2.3, +9.1], P 0.001 | +5.6 [+2.2, +9.3], P <0.001 | +5.6 [+1.9, +9.4], P 0.001 |
| leave one site out | rain + season | +4.4 [+1.1, +7.9], P 0.004 | +4.4 [+0.6, +8.6], P 0.01 | +4.4 [+0.9, +8.1], P 0.007 |

P is the share of resamples in which the full model was no better. Against rain
alone the gain is small (3-6% of the Brier score, 0.03-0.04 AUC) and survives
resampling by site but not, at conventional confidence, resampling by storm
week; the forward test is the strongest. Against rain + season, which isolates
the exposure term, the gain holds under every grouping. On lakes there is
nothing under any grouping, and the column is not shown for lakes on the map.
Rain windows: 100% of the accepted 48 h windows at lead 0 are complete, so the
90% rule changed no total in the fitting data; at leads 1-4, 88.5% are.

**Would the site have shown a figure?** Under the production rule (a figure is
withheld when more than 10% of transport weight comes from overflow-days
without rain data) the replayed pipeline would have shown one on 100% of sample
days at lead 0, 93% at lead 1 and 88.5% at leads 2-4; on wet days (48 h rain
over 10 mm) 95% and 90%, and on exceedance days 97% at every lead from 1 to 4.
So the abstentions fall mostly on dry, clean days, not the hard ones; the gaps
are Open-Meteo previous-runs outages, not the model declining.

**By lead, replayed.** The earlier lead-time figures changed only the spot rain
and kept the lead-0 exposure at every lead. `scripts/replay_ecoli_leads.py` now
recomputes the exposure from the rain forecast issued k days earlier through the
spill and transport models, as the live site does. Leave-one-year-out Brier for
the full model against rain only, all sites (rivers in brackets):

| Lead | replayed exposure | rain only | fixed lead-0 exposure (old test) |
|---|---|---|---|
| 0 | 0.0908 (0.138) | 0.0934 (0.142) | 0.0908 (0.138) |
| 1 | 0.0972 (0.148) | 0.1012 (0.155) | 0.0952 (0.145) |
| 2 | 0.0987 (0.151) | 0.1022 (0.157) | 0.0957 (0.146) |
| 3 | 0.1024 (0.157) | 0.1065 (0.164) | 0.0984 (0.150) |
| 4 | 0.1000 (0.153) | 0.1045 (0.161) | 0.0960 (0.146) |

The old test was optimistic by about 0.004 at four days; the full model still
beats rain alone at every lead. The replay applies production's missing-data
rule (at most 10% of transport weight without rain data) rather than rejecting
any missing value, so its availability matches what the site would show; its
remaining approximations are the analysis series for the 30-day and antecedent
features and the stitched previous-runs fields. Reliability is close to the
diagonal below 0.2 and above 0.4; the 0.2-0.4 bins over-forecast (forecast 0.25
and 0.35, observed 0.21 and 0.20, on 179 samples). Coefficients are stored as JSON
(`data/processed/ecoli_model.json`); `dipcast/model/ecoli.py` computes the
features live from the spot's own rainfall cell, with the rain window ending at
midday to match when the EA samples.

**Live scoring of the E. coli column.** From 15 September 2026 every spot's daily
exceedance forecast is logged with the rain it used, and once a day the build
fetches this season's EA samples at the 38 designated bathing waters into the
state directory. Each sample is matched, per lead, to the latest forecast for
that spot and day that was *issued before the sample was taken* (a forecast made
at 23:00 is not a forecast of an 11:00 sample), and scored the same way as the
spill forecasts: Brier and AUC against the training-period exceedance rate for
rivers and lakes, same-day and in-advance leads reported separately, with the
most recent samples listed against what the map said. It appears on the
verification page as results arrive, usually within a week of sampling. The 2026
season ends in September, so the first real read of this table is next May.
Samples come from two EA services. The bathing-water service is asked first,
since it publishes first, but from 28 Sep 2026 its gateway refuses GitHub's
runners (HTTP 403 whatever the User-Agent), so the build stops at the first
refusal. The Water Quality Archive holds the same results about 3-7 days later
and answers the runners: on 28 Sep it had 675 of the service's 702 samples as
statutory monitoring, all with identical counts. Each bathing water's archive
point is recorded as `wqa_point` in `data/raw/bathing_waters_inland.json` by
`scripts/map_bathing_waters_wqa.py`, which matches samples, not just distance. A
sample found in both is taken from the bathing-water service. Each fetch attempt
is recorded (`ecoli_samples_status.json` in the state directory), and a site no
source answered for keeps its earlier samples. If no source answers at all, the
build log, the Actions run page (as an annotation) and the verification page say
so, instead of showing zero scores as if nothing had been sampled yet.

**Beside the Environment Agency's daily risk prediction (3 Oct 2026).** The EA
publishes a prediction for each designated bathing water every day of the season,
"normal" or "increased" (`stp-risk-prediction.json?predictedOn=<day>`); where it runs
a pollution risk forecast (PRF) for the site, `prfOriginType` is `PRF_PROVIDED`.
`scripts/compare_prf.py` fetched all 139 days of the 2026 season (15 May to 30 Sep)
and found that none of the 38 inland bathing waters had a PRF on any day; 34 appear
in the predictions at all, and there "increased" follows a posted notice (a
pollution incident, harmful algae), not a forecast. It scores the prediction in
force when each EA sample was taken, read as 1 or 0, beside SwimSignal's latest E.
coli estimate issued before the sample. The estimate has been logged since 15 Sep,
so the two meet on 30 samples at 22 sites (16-28 Sep), 2 of them over 900: Brier
0.072 for SwimSignal, 0.133 for the EA, 0.079 for the long-run rate, and neither
warned before either exceedance. Too few to judge. Over the whole season the
prediction was "increased" before 13 of the 598 samples it covered (1 over 900) and
"normal" before 44 of the 45 over 900. The EA's gateway refuses GitHub's runners, so
the script runs by hand and `data/processed/prf_comparison.json` is committed; from a
home connection it refused twice, after 55 and 62 requests, and answered again within
90 s, so the script pauses 2 s between requests, retries a refusal once after 90 s,
and caches each day's response under `DIPCAST_CACHE/ea_prf/`.

**Live scoring rules for the spill forecasts (16-17 Sep 2026).** The forecast
scored for each overflow and day is the latest one issued by 08:00 local time on
the issue day, so "today" is the forecast a swimmer had at breakfast, not the
end-of-day estimate the earlier rule (latest issue of the day) produced. Issue
days with no forecast by 08:00 are missed deadlines: counted and listed on the
verification page, not scored. An overflow-day counts as "no spill" only if the
poller recorded that overflow with a known status at least six times that day,
from a feed whose freshest `LastUpdated` was under 6 h old (a feed with no
`LastUpdated` at all is not current; see the correction of 2 Oct below), with no unobserved
stretch longer than 8 h (6 h until 1 Oct 2026; see the correction below;
counting midnight to the first poll and the last poll to midnight), and once
the next day. `live_coverage.parquet` holds one row per
overflow per day: known, unknown and stale poll counts and a 48-bit mask of the
half-hour slots observed, from which the scorer derives first and last
observation and the longest gap; a repeated poll in the same slot adds nothing.
`poll_log.parquet` records each poll's per-company row count and feed age, so
outages and stale feeds are visible (six companies re-stamp every record each
refresh; Northumbrian and Southern stamp a record only when it changes; South
West Water publishes no stamp). Before this, any polling at all on a day counted
as coverage for every overflow, and the history file, which keeps one row per
distinct status, could not say which overflows had actually been seen. Scores
from before the rule change are withdrawn; the table restarts as coverage
accumulates, and the page reports the same scores under a stricter 6 h gap rule
alongside. The withdrawn scores are kept in
`data/processed/verification_live_oldrule_2026-09-28.json` (49,380
overflow-days, 17-27 Sep 2026, a dry spell). They were not good: forecasts
averaged 2.7% against 1.3% observed, forecasts between 10% and 70% verified at
a third to a half of their stated value, and only at United Utilities, the one
company in the training data, did they beat a flat forecast at the period's
own spill rate. The climatology baseline divides each overflow's annual spill count
by 365, an approximation: the returns count spills by the 12/24-hour block
method, and `scripts/spill_day_ratio.py` finds 1.00 spill-days per counted
spill pooled over 5,886 United Utilities site-years, which supports the
approximation there without establishing it per overflow or company.

Correction (29 Sep 2026): these rules were written on 16-17 Sep but reached the
live site on 28 Sep, when the commit carrying them was pushed to main (16:51
UTC). The coverage file shows it: no overflow-day row from 16-27 Sep carries a
half-hour observation mask, and 98% of 28 Sep's rows do. Under the gap rule a
day without a mask can never be scored, so the live table was empty on 29 Sep
(0 of 70,663 candidate overflow-days); 29 Sep is the first day that can be
scored, if that day's polls meet the rule. The
scorer now reports the first masked day (`observations_from`) and the
verification page says why the table is empty.

Correction (1 Oct 2026): the 6 h gap rule assumed the poller ran every 30
minutes, as the workflow asks. GitHub starts the schedule when it can: on 29
and 30 Sep it ran 4 and 5 times, with daytime gaps of 6.4-6.8 h (03:25, 09:48,
16:15, 21:05 on the 29th), so every one of the roughly 14,000 masked
overflow-days failed the gap test and nothing was scored (0 of 80,753
candidates on 1 Oct; the other steps passed about 5,040 a day). Over 17-30 Sep
the longest daily gap was under 6 h on 10 days of 14 and under 8 h on all 14,
so at 6 h about a day in three would have been lost for good, and the first
three masked days all were. From 1 Oct the limit is 8 h, which would have
scored 4,893 overflow-days on 29 Sep and 4,936 on 30 Sep, and the stricter
rule reported alongside is the old 6 h, so the effect of the change stays
visible. The next-day rule still catches a single event that ends late; what
8 h gives up is a second event that starts and ends unseen inside one stretch.
Established by running the scorer's own functions on the `state` release of
1 Oct 10:32 UTC.

Correction (2 Oct 2026): South West Water's feed carries no `StatusStart`,
`LatestEventStart`, `LatestEventEnd` or `LastUpdated` on any row. Three things
followed. A spill was found only from the event fields, so none of its
overflows ever counted as spilling, although the history holds rows marked
discharging (74 overflows on 29 Sep, 165 on 30 Sep): its 135 scored
overflow-days all read "no spill", base rate 0, skill -0.52. The history's
de-duplication key (site, status, status start) was the same on every poll, so
each overflow kept one row per status and a later day overwrote an earlier
one. And the feed-age test read a missing stamp as current. Now: a row marked
discharging with no event times is a spill on the local day of that poll; a row
with no status start is kept once per local day polled, at every company.
Anglian, United Utilities and Wessex have such rows too (294 in the 2 Oct 14:08
snapshot, 18 of them with event times), so the history grows by about 300 more
rows a day, and an event the old key would have overwritten is now kept: their
spill-day counts can only rise from now on. On the 2 Oct history, which the old
key had already de-duplicated, no other company's count changed; and a feed with no
`LastUpdated` is never current, so South West Water's overflows are not scored
at all (`unstamped_feed_days` applies this to the days polled before the
change, from `poll_log.parquet`, ignoring rows from before the log recorded feed
ages on 28 Sep 16:51 UTC, when every company's age reads as missing). The feed sees only what is discharging at the
moment of a poll, a few times a day, so a day of "not discharging" polls cannot
support "no spill". Its scores are withdrawn: 14,643 of the 14,778 overflow-days
scored on 2 Oct remain, by the scorer run on the `state` release of 2 Oct
14:08 UTC.

The scorer also reports why each unscored overflow-day was not scored
(`uncovered_by_reason`: before observations, feed not current, gap, too few
polls, no poll the next day), and a `scoring_window` from `observations_from`
to yesterday. Days before the masks existed cannot be scored, so they are
counted apart (`n_before_window`) rather than in the window's candidates. On
2 Oct's state, 60,300 of the 65,805 unscored overflow-days were before
observations, 5,325 failed the gap rule (5,025 of them on 28 Sep, when the
masks began in the afternoon) and 180 were South West Water's.

A company feed that fails, or returns no rows, keeps its last snapshot in
`live_latest.parquet` with status -3 (feed down) and the time it last answered;
a spot's "Right now" tile names the company and that time, and counts its
overflows as not reporting. The build warns when a company returns no rows. It
still publishes when none does, with every overflow marked feed down: not
publishing would also freeze the rain forecasts and leave the old statuses on
the page with no note.

**Algae (an observation, not a forecast; 28 Sep 2026).** At every sampling visit to a
bathing water the EA sampler records one of four levels of algae: none, a trace (1-2
items), some at intervals (3-6), or enough to be objectionable (more than 6). This is
"Bathing Water Profile : Algal Bloom", determinand 4824 in the Water Quality Archive,
recorded against the same sample as the E. coli result. On 28 Sep the archive held 2,925 of
these checks at the 38 sites since 2020. From 2023, algae was seen at 310 of 1,304 lake
checks (63 objectionable) and 64 of 1,012 river checks (11). 58 of the 63 objectionable
lake checks were at four sites: the Serpentine (21), Colwick (15), Frensham Great Pond (12)
and Hampstead's Ladies' Pond (10).
There are no chlorophyll, cyanobacteria-count or toxin results at these sites, and the
check does not tell blue-green algae (which can be toxic) from harmless kinds. Too few
objectionable checks to fit a model, so the page shows each bathing water's latest check,
dated, and the season's tally. `dipcast/algae.py` fetches the season (from 1 May) once a
day into `algae_checks.parquet` in the state directory. A failed or empty fetch keeps the
checks already held, and nothing about it stops a build. The file is not in the state
release, because the archive can refill it on any build. Where it adds something: the
Serpentine has no monitored overflow upstream, so the forecast has nothing to flag, and
in 2026 the sampler saw algae there at 18 of 20 visits, 12 of them objectionable.

**Versions.** Every logged forecast carries a stamp of the spill model,
calibration map and E. coli model (content hashes), the code version and git
sha, and the weather source (`forecast.model_version`). Live scores are broken
down by stamp on the verification page, so a change starts a new row rather
than being averaged into the old one.

**Level checks (28 Sep 2026).** Beating climatology says little in a dry
spell, because climatology knows nothing about the weather. The live table
therefore also reports the mean forecast against the observed spill rate,
overall and per company, and the score of a flat forecast at the period's own
spill rate. That flat forecast uses hindsight, so it is not a rival, but a
forecast that scores worse than it is pitched at the wrong level. From 28 Sep
each logged overflow-day forecast also carries the target-day rain it assumed
(`rain_mm`), and the page groups scores by it: excess on forecast-dry days
means the model's floor is too high, excess only on wet days points at the
rain forecast or the model's response to rain. A seasonal climatology was
considered and dropped: United Utilities' 2023-25 events put September at or
above the annual average (monthly factors 1.04, 1.06 and 1.91), so it would not
have made a dry September harder to beat.

**Missing rainfall is unknown, not dry (16-17 Sep 2026).** The E. coli rain windows
used to count timestamps rather than finite values, so a null-filled forecast
passed the completeness check and summed to 0 mm; the daily spill features had
the same failure. Both now require 90% (windows) or 20 of 24 (days) finite hours
and return NaN otherwise. The window is (t − 48 h, t]: exactly 48 hour stamps,
each the rain in the hour ending at that stamp (until 17 Sep it summed 49). An
accepted window with a few missing hours still sums only the hours it has, so
the finite fraction is kept (`rain_48h_coverage` in the API) and the evaluation
reports the gain on complete windows only alongside all accepted ones. Further, the transport step reports the share of weight arriving
from days without rain data, a day with more than 10% missing gets no figure on
the map ("no data"), overflow-days forecast without rain are excluded from live
scoring, and a build in which most forecasts fail or lack today's rain exits
non-zero so the previous site stays up. The live forecast's daily grid now starts
`history_days(travel)` days back, the same rule as the hindcast, so a 48 h travel
time contributes to today (it started at yesterday before, and did not).

## Known limits

- The lead-time tests approximate the features: the target day and the days
  between issue and target use lead-appropriate forecasts; the 30-day and
  antecedent windows use the analysis series, since on the issue day they are
  almost all observed. Open-Meteo's previous-runs fields give, for each hour, the
  value from the run issued k days earlier, so a lead-k day is stitched from
  several runs rather than one run's trajectory; a single-run replay (their
  Single Runs API) would be stricter.
- The per-lead isotonic calibration is fitted on 2025 and the map applies it to
  2026; `verify_leads.py` also reports a cross-fitted score (each month
  calibrated by a map fitted on the year's other months) as the honest estimate
  of what it does for an unseen day.
- The algae check covers only the 38 designated bathing waters, is a sampler's look at
  the water rather than a lab test, and reaches the archive about a week after the visit.
  From October to April it is last season's final check, dated as such.
- The E. coli column is validated on river bathing waters in the May-September
  sampling season. On lakes it has no ranking skill and is not shown. The EA
  takes no samples from October to April (all 2,165 samples in
  `bwq_samples.parquet` fall in May-September), so then nothing tests it. It
  is still shown then, marked † on the page, but since 1 Oct 2026 it does not
  count towards the level out of season: on that day it alone had put 35 of 89
  spots on high or very high while the spill forecast read low at 78 of 87,
  which would have kept the list red all winter on an untested figure. The
  spill forecast still answers to rain, so a wet autumn day still turns amber
  where the overflows upstream are likely to spill. Its day-of-year term is held at
  30 September's value from October to January and 1 May's from February to
  April (`ecoli.season_day`): left to run, the fitted curve, with rain and
  exposure fixed, rose from 38% on 30 September to 48% in mid-December with
  nothing to check it, and the term adds no skill even in season (rain + season
  scores level with rain alone). The fitting samples all fall inside the held
  range, so the fitted model is unchanged.
- OS Open Rivers has small breaks at weirs, mills and culverts, and side
  channels (mill streams, leats) that are not connected upstream. SwimSignal joins
  653 headwater nodes to a foreign dead-end within 60 m that carries real
  network, and a pin on a channel with under 5 km upstream adopts a nearby
  channel with at least five times more. Without this the Thames overflows
  were invisible from Wolvercote Mill Stream and the Cam stopped 6 km above
  Cambridge. Reported as `adopted_main_channel` in the API.
- Snapping outfalls to the network: 85% sit within 750 m of a link ("high"
  confidence). A second pass to 1.5 km recovers 9% more, preferring a link whose
  name shares a distinctive word with the recorded receiving watercourse
  ("medium", 501 outfalls) and otherwise taking the nearest ("low", 862, weight
  scaled by 0.7). 475 outfalls to the sea or estuaries are excluded by design and
  335 inland ones (2%) remain more than 1.5 km from any link. Confidence is
  reported per contributor.
- The overflow-exposure percentage combines verified spill probabilities with
  die-off and dilution weights that are physical estimates, not calibrated
  against water samples, and it treats upstream spills as independent when they
  share the same rain. The site labels it an exposure index for that reason;
  the E. coli column is the calibrated quantity.
- Dilution uses network length as a proxy for flow. Lake volume is not modelled;
  lake crossing uses a fixed slow advection speed.
- Daily resolution. Sub-daily timing of a plume is not resolved.
- Annual returns before 2024 carry old or no overflow IDs; `dipcast/ids.py` resolves 93% of rows (100% of UU 2023, 92% of UU 2021-22) via the Hub lookup, then site name and grid reference.
- Only United Utilities publishes event-level history on ArcGIS, so the site
  calibration layer covers their overflows. Others use the pooled model with
  annual-return covariates, and the live poller accumulates their history. The
  spill model was trained on United Utilities only; the live verification page
  scores every company's live-feed overflows separately ("By water company"),
  which is the running check that it transfers. (Until 15 Sep 2026 the live
  scorer scored nothing: DuckDB returned its DATE columns as timestamps and the
  join to observed spill days silently matched no rows. Fixed, with a test.)

## Status (17 Sep 2026)

Working end to end on a local machine: click anywhere on a river or lake in
England and get a "right now" risk from live status plus a five-day forecast,
with the contributing overflows listed and drawn on the map. Verified on a
held-out year (table above). Unit tests (run by the site workflow before every
build) cover the label exploder, rainfall features, the risk combination,
missing-rain handling, the travel-time history window, the decision-time and
coverage rules of the live scorer, the issued-before-sample rule of the E. coli
scorer, coverage accumulation in the poller, the build-health guard, and the EA
sample fetch (the archive stands in when the bathing-water service refuses, a
total failure is flagged, a partial one keeps earlier samples, requests carry the
contact User-Agent).

Done since v1: reliability release of 16-17 Sep (missing rain is unknown not dry;
strict 08:00 headline with missed deadlines reported; per-overflow observation
masks with a longest-gap rule and feed-freshness check; version stamps on every
forecast with live scores by version; 48-stamp rain windows with coverage kept;
bootstrap intervals by site and by storm week and the rain + season comparison;
replay under the production missing-data rule with availability reported;
decision-time and coverage-gated live scoring; issued-before-sample rule for the
E. coli scorer; travel-time history window shared with the hindcast; build-health
guard; leave-one-site-out, forward-in-time and bootstrap tests of the E. coli
model with the lead-time exposure replayed; exposure shown as a 0-100 index,
E. coli column withheld on lakes and flagged out of season; stale-forecast
banner); Southern Water live feed (all nine English companies now live);
E. coli exceedance model on the map, logged and scored live against new EA samples (15 Sep); per-lead isotonic calibration replacing Platt (15 Sep); second-pass outfall snapping (85% to 94%); shareable URLs; WFD lake polygons with a lake-size dilution term; recency
weighting in the site calibration; production refit on all years; verification
against archived forecasts by lead time (below); `Dockerfile`; a launchd plist
in `deploy/` for the 20-minute live refresh (not installed automatically).

Not done yet, in the order I would do them:

1. Let the coverage-gated live scores and the E. coli live scores accumulate
   (a full bathing season, May-September 2027, for the latter) and publish the
   river-only results against rain alone with intervals.
2. Put the site in front of a few swimmers or monitoring officers and find out
   which decision it changes; nothing below matters until that is known.
3. Replay the spill model's lead-time test with single model runs rather than
   the stitched previous-runs fields.
4. Let live history accumulate for the eight companies without event feeds,
   then fit their site calibration.
5. Per-lake residence time (needs volume; WFD gives area only).
6. Dwr Cymru live status. On 29 Sep 2026 their storm-overflow map reads a
   public ArcGIS layer (`services3.arcgis.com/KLNF7YxtENPLYVey/.../Spill_Prod__view/FeatureServer/0`,
   2,362 overflows, status as text, no token). It carries no licence text, so
   ask Dwr Cymru for reuse terms before building on it. It matters for the
   English Wye spots, which have Welsh overflows upstream.

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

The API answers at http://localhost:8000/api/forecast (and /docs); its / sends you to the
site, whose map `scripts/build_site.py` builds. Live status refreshes in-process every
`DIPCAST_REFRESH_MINUTES` (set it, e.g. `DIPCAST_REFRESH_MINUTES=20`); with it
unset, run `scripts/refresh.py` on a schedule and call `POST /api/reload`
(`deploy/com.ethanbuckley.dipcast.refresh.plist` does this on macOS). Mutable
files (live polls, the overflow table, the forecast log, live scores) go to
`DIPCAST_STATE` if set, else `data/processed`. All raw pulls are cached under
`data/cache/` so re-running the ingestion is cheap. Requests to
environment.data.gov.uk carry the User-Agent `dipcast/<version>
(+https://github.com/ethanbuckley/swimsignal)`; a fork should set its own with
`DIPCAST_USER_AGENT`. `scripts/verify_leads.py
2025` reproduces the lead-time table; `scripts/validate_ecoli.py` then
`scripts/validate_ecoli_combined.py` reproduce the E. coli tables.

Every forecast is logged (coordinates, time, values; nothing about the user)
and scored once its days have passed, using the accumulated live polls. The
result is public at `/verification`, alongside the offline tests. `/terms` and
`/privacy` hold the plain-English terms and privacy notice.

## How the free site works

`spots.csv` lists the spots: the 38 Environment Agency designated inland
bathing waters and about 50 well-known river and lake spots. A river spot
names its river in the `river` column (see Spot placement). Add one by pull
request, or ask for one with the "Request a spot" issue template; it appears
in the next run. Inclusion is not a statement that a spot is safe.

A request made with that template gets one automatic comment
(`.github/workflows/spot-request.yml` runs `scripts/spot_request.py`). The
script reads the form's Location as latitude and longitude (decimal, degrees
minutes and seconds, or a Google Maps address that holds them) or an OS grid
reference, places the point with `locate_pin` as the build places a
`spots.csv` row (a river named in the spot's name is the hint), counts the
monitored overflows within 60 km upstream, runs `build_site.placement_check`
and proposes a `spots.csv` row. A location it cannot read gets a request for
coordinates, and editing the issue runs it again and updates the same
comment. A what3words address or a place name is looked up only when the
repository secret `W3W_API_KEY` (what3words) or `OS_API_KEY` (OS Names API)
is set. The workflow answers only issues with the `spot-request` label, which
the form adds only if that label exists in the repository. It can also be run
from the Actions tab with the form's fields; the answer then goes to the run
summary.

`spots-osm.csv` adds 16 spots taken from OpenStreetMap, with the same columns
plus `osm_id`, ids starting `osm-`, and `source` "openstreetmap". It is a
separate file because the ODbL would cover any file that mixed OpenStreetMap
rows with ours (`LICENSE-DATA.md`). `scripts/osm_spot_candidates.py` asks the
Overpass API for England's bathing places, swimming areas, `sport=swimming`
and swim-like names, drops pools, signs, the sea and duplicates within 150 m,
places each on the network and writes `data/raw/osm_swim_candidates.csv`
(115 candidates on 3 Oct 2026). A person then reads every row and copies the
natural river and lake spots the public can reach into `spots-osm.csv`, with
`river` typed for each river spot. The build reads both files, runs
`placement_check` on both, and credits OpenStreetMap on the terms page, in
`credits.spot_locations` and on each such spot's page.

**Any other point (October 2026).** A click on the map away from a listed spot
gets a forecast too, worked out in the browser (`src/dipcast/site/anypoint.js`)
from files the build writes under `data/anypoint/`
(`scripts/build_any_point.py`, whose docstring lists them): every river
link's upstream overflows, in a form that gives the API's distances and
dilutions for a click anywhere along the link; a packed index of the links,
by 0.25° square, for snapping a click; the WFD lakes with their inlets; and
every overflow's spill probability for each day. The page then does what the
API does with `gauge=False`: the same placement (lake polygon, nearest link
within 1.5 km, a side channel traced as the main river), the same transport,
calibration and combination, and the same card, marked "Unlisted point: not
hand-checked", without the E. coli estimate (it needs rain at the spot
itself, which only listed spots get). Squares with no overflow data, which
is most of Wales and Scotland, get no forecast. The tracing runs once per
network release and is cached in the state directory (`anypoint_links.pkl`);
only links downstream of an overflow that moved are traced again. Rain for
all overflows is about 1,600 Open-Meteo cells against the spots' 300, and on
3 Oct 2026 Open-Meteo refused this build after 600 locations in one minute,
so each build fetches at most 300 more cells, oldest first, and uses cached
ones up to 24 hours old; an overflow with neither counts as having no rain
data, and the card says when its rain is older than the issue time.

Each spot has its own page, `spot/<id>/`, written by `build_site.write_pages`:
the same map page with the spot's name, a one-line description and absolute
share-preview tags in its head (today's level is left out, because messaging
apps keep a preview for days), plus `sitemap.xml`. Links shared from the site
use these addresses, and old `?spot=` links are rewritten to them. Every page
sets a relative `<base>` at the site root (`./` at home, `../../` on a spot's
page). The page moves between the list and spots with `pushState`, and a
`<base>` is fixed as the page loads, so its links do not follow the address
into `spot/<id>/`; being relative, the pages work at any address.
Share links, previews and the sitemap need the absolute one: the repository
variable `DIPCAST_SITE_URL` (set it when adding a custom domain) or, unset, the
repository's GitHub Pages address. `icons/og.png` is the preview image
(`uv run --with pillow python scripts/make_share_image.py` redraws it after a
name change). `sw.js` keeps the page and the latest forecast on the device, so
the home-screen app opens without signal and says how old the forecast is. A
saved spot is kept in the browser's local storage (and by the alert service, if
alerts are turned on), and `saved/` lists the
saved spots with their five days; `saved/#spots=a,b` offers a list someone
shared (after the `#`, so it never reaches a server). An iPhone's Home Screen
app has storage of its own: spots saved in Safari do not appear in it (checked
in the iOS 27 simulator, 29 Sep 2026), and the Saved page says so. The Feedback page opens the reader's email app with a message to
hello@swimsignal.co.uk (the site has no server, so nothing is sent by the
page itself) and asks whether the forecast changed what the person did.

**The level on the map and in the list** is the worst of four things, worked
out by `src/dipcast/site/levels.js` (`risk()`), which the page loads and the
build runs in Node for the alerts, so the two cannot disagree: the spill forecast's level; on
rivers with overflows upstream, the E. coli column in bands of under 10%, 25%,
50% and over (the minimum inland standard lets about one sample in ten be over
900), in the May-September season only (out of season the column is shown, marked
†, but not counted: see Known limits); the Environment Agency's rating, where "poor" (advice against bathing applies
all season; the local authority that controls the water issues it, not the EA)
makes every day at least high; and the sampler's algae
check if under two weeks old. On 29 Sep 2026 the spill forecast alone had all
nine Thames spots on "low", and all 13 poor-rated bathing waters on "low" for
that day (7 of them for the next day too). The ratings are in
`data/raw/bathing_water_classifications.json`, written by
`uv run python scripts/fetch_classifications.py`: run it after each year's
classifications are published (the 2026 ones are due in December) and commit
the file. It is a file rather than a fetch in the build because the EA's
bathing-water service has refused GitHub's runners since 28 Sep 2026 (seen on
its sample endpoint; the ratings come from the same service). The EA's short-term advice
against bathing after an incident is not included, for the same reason and
because the service sends no CORS header, so a browser page elsewhere cannot
read it either: on 29 Sep 2026 it covered Ham and Kingston and Frensham Great
Pond (algae). Each bathing water's page links to the EA's page instead.

**Where there is nothing to forecast (3 Oct 2026).** A spot with no monitored
overflow within 60 km upstream gets a plain level of its own, "No sewage risk
from monitored overflows" (teal), and an isolated lake, which no river reaches
in the network, "No river connection: overflows cannot reach this lake" (grey).
Under either, the page says "Other risks apply: algae, wildlife, runoff and
bathers. Check the signs at the water." and gives the rain in the 48 hours to
midday today as "12 mm of rain in the last two days". Neither shows an E. coli
estimate, since the model was fitted on sites with overflows upstream (before
this, the day-by-day table showed the build's figure on a river with nothing
upstream: 35% today for the Duddon at Birks Bridge on 3 Oct). A rating of
sufficient or poor, or a recent algae check, still sets the level where it
raises it, but an excellent or good rating no longer makes such a spot "low".
In the 3 Oct 14:48 local build that was 17 and 2 of the 89 spots; 7 of them
(Colwick, the three Hampstead ponds, the Serpentine, Henleaze and Cotswold
Country Park) had read "low" by their rating, so they now leave the "Low risk
only" count and are not offered as "Lower risk nearby".

**One day.** A spot's five days are buttons. Picking one shows, in place of the
summary of today and tomorrow, that day's level and what set it: the spills
(how many overflows are expected to spill, the exposure index, and the three
overflows most likely to reach the spot that day, from each overflow's spill
chance for every day, `p_spill_days`), the water quality, the rain, and how good
a forecast that far ahead has been. That last is `lead_skill` in `spots.json`,
each day's Brier skill as a share of the same-day forecast's, from
`verification_leads_2025.csv` (spills, lead-calibrated and cross-fitted) and
`ecoli_model_eval.json` (water quality on rivers), held from rising with the
days: 1.0, 0.84, 0.79, 0.70 and 0.59 for spills and 1.0, 0.75, 0.69, 0.55 and
0.55 for water quality on 29 Sep 2026. The open day is in the address
(`spot/<id>/#day=2026-10-01`), so a shared link opens it, and a day on a Saved
card opens the spot on that day. The map has a day picker too, which colours
the spots, and the "Low risk only" switch, by one day. On a phone a spot's page
starts with a small map of where it is; a tap opens the full map there.

**Beside the forecast (1 Oct 2026).** Each spot's page also carries, as
observations and context rather than inputs to the level: the Environment
Agency's nearest level gauge, preferring one on the spot's own river
(`flows.nearest_level_station` with a river-name preference; without it
Burnsall on the Wharfe got Hebden Beck, a tributary 3 km away, over Netherside
Hall on the Wharfe 6 km up), with the latest level, the gauge's usual range, a
word for where the level sits and a link to the EA's page for it; on 1 Oct 62
of 89 spots had one, 31 on the same river. The spot's river is the `river`
column, so Symonds Yat now asks for a gauge on the River Wye, not on
"Afon Gwy". The EA's "latest" reading can be weeks old: on 2 Oct Salisbury's
was 708 h old and Temple Sowerby's 77 h. A reading more than 24 h old is not
shown as the level now. It keeps the station and the time, moves the value to
`last_level_m`, sets `stale: true` with `age_hours`, sets `level_m` and
`index` to null and `label` to "unknown" (`flows.reading_fields`). The API's
own forecast publishes the reading the same way and does not use it to scale
travel speed. The EA's API is OGL and asks for the
line "this uses Environment Agency flood and river level data from the
real-time data API (Beta)", which the footer, the terms and the data credits
carry. Open-Meteo's daily high, sunrise and sunset (three daily variables over
five days weigh one call per spot per build). Both are fetched by
`build_site.attach_river_levels` and `attach_weather` after the forecasts, in
the refresh step only, and a failure leaves a spot without them. The list and
the Saved page say which day this week has the most spots at low (`bestDay` in
`levels.js`). A spot's page can draw its forecast into a picture for a swim
group's chat (a canvas on the device; nothing is uploaded until the share
sheet) and log "I swam here today", kept in the browser with the day's level
and listed on the Saved page, where each entry links to the feedback form with
the spot and the day filled in.

**Too high to swim (3 Oct 2026).** A high river and a flood are a different
hazard from pollution, so they never change the level; a spot's page says them
in one line under the headline, kept in view on every day's view, ending "A
separate hazard, not part of the pollution level." "River high: the gauge at
Addingham is above its usual range" when the latest reading at a gauge on the
spot's own river is above the top of its usual range (index over 1.0). "River
rising fast: the gauge at Addingham rose 0.40 m between 08:45 and 14:30" when
the gauge's readings in the six hours before the build (the measure's last 24,
`_limit=24`, 15 minutes apart) rose by more than a fifth of the usual range.
"Flood alert in force nearby (Environment Agency): River Wharfe at Ilkley" for
the most severe flood alert or warning in force for a flood area within 10 km
(`/id/floods?lat&long&dist=10`, which measures to the area, not its centre),
linked to the area's page on check-for-flooding.service.gov.uk. Severity 4,
"Warning no longer in force", is left out: on 3 Oct 2026 it was the only item
the service held for England. The build asks spot by spot only when the
national list holds an alert in force, so on most days the floods cost one
request. A gauge on another watercourse, or a lake's,
gives no river word. The words are `flows.flow_state` and `flows.flood_alerts`,
fetched by `build_site.attach_flow_state` after the levels, in the refresh step
only; the comparison's "Local warnings" row names the flood alert too. The page
(`flowFacts` in `experience.js`) says nothing from a reading over 24 h old, a
rise whose last reading is over 6 h old, or a build over 24 h old, so a stale
reading never says "River high". The flood API is the same OGL service the
river levels come from, already credited.

**Water temperature (3 Oct 2026).** `build_site.attach_water_temperature`
gives a river spot the latest reading of the nearest Environment Agency
water-quality sensor within 15 km (straight line) whose river has the spot's
river's name (`same_river`), that the network joins to the spot, on the same
side of the tidal limit, read in the last 24 h (`ingest/water_temperature.py`).
Two Hydrology API requests per build: the temperature readings since
yesterday (about 1 MB) and the active temperature stations (about 0.5 MB). The
sondes have no `riverName`; the river and the place come from the label
("STOUR_BURES MILL_E_201704"). The API's times have no zone and are UTC (its
Evesham level series matched the flood-monitoring API's Z times on 3 Oct).
The page shows "Measured at Bures Mill on the Stour, 10.9 km downstream, 1 h
ago" under the figure, with the age counted when the page is read; a spot with
no sensor shows nothing, never an estimate, and lakes get none. In a local
build on 3 Oct, 99 sensors had read in the last day and 2 of the 58 river spots
got one (Friars Meadow and Dedham, both on the Stour); the six Thames sondes
within 15 km of Ham and Kingston are all on tidal links and are passed over.
A failed request leaves every spot without and does not stop the build; the
build summary's `water_temperature` is the count.

**Alerts** (`push/`, set up by hand: `push/README.md`). A Cloudflare Worker
keeps, for each browser that turns alerts on from the Saved page, its push
address and the ids of its saved spots, and nothing else. Each build writes
`data/alerts.json`, every spot's level and headline by `levels.js`
(`scripts/alerts.js`, which needs Node; the runners have it). Every 2 minutes
the Worker compares it with the previous one and queues a notification for any
saved spot that has just turned high or very high, at most once a spot in 20
hours, and sends them 15 a run (about 450 an hour on Cloudflare's free plan). The page shows the switch only when the repository variables
`DIPCAST_PUSH_URL` and `DIPCAST_VAPID_PUBLIC_KEY` are set, and the privacy
notice gains its alerts section only then. On an iPhone, alerts work only in
the Home Screen app.

**How it looks** is set out in `docs/DESIGN.md`: two typefaces served from the
site itself (Source Serif 4 for headings, Source Sans 3 for the rest, in
`src/dipcast/api/static/fonts/` under the SIL Open Font License, so no third
party sees a request), one corner radius, hairline borders, the four level
colours as the only strong colours. The prose pages share
`src/dipcast/api/static/page.css`; the app page repeats its tokens and header
inline so that it paints before any stylesheet arrives and works offline on its
own, and a token changed in one must be changed in the other. The prose pages'
favicon and Home Screen icon are copies of the site's icons, kept in
`src/dipcast/api/static/icons/` so that the API server's pages get them too.

**Data files and the embed** (3 Oct 2026). `data.html` lists every file under `data/`, what it
holds, its fields and its licence. Its words are `src/dipcast/api/static/data.html`;
`build_site.write_data_page` fills in each file's size and adds a row, with a build warning, for any
file in `data/` the page does not describe, so a file added to the build belongs on that page too.
`embed.html?spot=<id>` is one spot's card (headline, five days, caveat, issue time, a link back and
the credits) for a club's or a council's site to show in an iframe. `embed.js` draws it with
`levels.js`, so it says what the spot's page says. The About page gives the snippet, and the terms
allow it with the credits intact.

`.github/workflows/site.yml` is scheduled every 30 minutes and also runs on
every push. GitHub starts scheduled runs when it can: the 113 builds of 13-28
Sep 2026 were a median 2.9 h apart and at most 8.1 h, so no step depends on a
run starting at a particular time, and the map's "Stale" banner waits until the
forecast is 8 hours old (at the earlier 2 hours it would have shown 48% of the
time; at 8 hours, never in that period). The job restores the mutable state (live polls, forecast log, rainfall cache) from
the Actions cache, or from the rolling `state` release if the cache is cold;
downloads the river network from the `data-v1` release (113 MB, too big for
git); runs `scripts/build_site.py`, which polls the nine live feeds, rebuilds
the overflow table, forecasts every spot with `forecast_point`, scores logged
forecasts against the accumulated polls, and writes `site/`; saves the state
back to the cache and, when the release copy is over 12 hours old, to the
release (GitHub runs the schedule only a few times a day, at irregular times);
and deploys `site/` to GitHub Pages. Nothing is committed by the job except
`HEARTBEAT.md`, so the repository does not grow. The first scheduled run after
that file is 25 days old rewrites it, because GitHub disables the schedule of
a public repository after 60 days without activity.

The click-anywhere API (below) is the same code behind a FastAPI server. It
is what to run when someone needs forecasts for arbitrary points or an API,
and it costs about £8 a month on Fly.io; the static site costs nothing.

## Before taking money or running ads

Checked on 29 Sep 2026 against the licences, the legislation and the providers'
own pages, in a solicitor-style review by an AI assistant: not legal advice, and
no substitute for a solicitor before any of these happens.

- **Is SwimSignal a business?** Probably not yet: no income, no ads, nothing sold.
  But the Consumer Rights Act 2015 (s.2(2)) catches anyone "acting for purposes
  relating to" a business, and building an audience for a planned launch could
  meet that. The terms are therefore written as if the Unfair Contract Terms Act
  1977 and the CRA apply: no exclusion of liability for death or personal injury
  caused by negligence (UCTA s.2(1), CRA s.65), and the "without responsibility"
  disclaimer yields to that (a disclaimer that stops a duty arising counts as an
  exclusion for a business, UCTA s.13).
- **Does the Met Office's share-alike licence reach the forecasts?** On this
  reading, no. CC BY-SA 4.0 imposes share-alike only on "Adapted Material",
  material changed in a way that needs the licensor's permission (s.1(a),
  s.3(b)), and imposes no conditions on uses that need no permission (s.8(a)).
  Database right protects against extracting or re-using all or a substantial
  part of the data (Copyright and Rights in Databases Regulations 1997, reg 16).
  Forecasts, levels and scores computed from the rainfall do neither. The
  48-hour rainfall sums on the site are an insubstantial part, and are labelled
  CC BY-SA anyway. The residual risk is contractual: Open-Meteo's Met Office page
  says derived products "should" be shared alike, though its terms of use do not
  impose it. For a paid data product, request a CC BY model (`models=`) and
  re-check the spill and E. coli models against its rainfall, or get this
  reading confirmed.
- **Hosting.** GitHub's rules do not allow GitHub Pages to host an online
  business, so a paid SwimSignal needs another host.
- **Rainfall API.** Open-Meteo's free API excludes sites with subscriptions or
  advertising; those need a paid plan, whose terms then apply.
- **Map tiles.** The OpenStreetMap Foundation's tile policy warns commercial
  services, and those that seek donations, that access may be withdrawn at any
  point; use a tile provider before either.
- **Insurance and a company.** Before charging, get public liability and
  professional indemnity insurance, and trade through a company or LLP.
- **Data protection.** The privacy notice names the controller and a private
  contact, and says complaints are acknowledged within 30 days (UK GDPR Art 13;
  DPA 2018 s.164A, from 19 June 2026). The site probably owes the ICO no fee now
  (personal and household processing is exempt); payments or ads would end that.
- **Email alerts.** The privacy notice promises an update before they exist:
  collect addresses only with clear consent, put an unsubscribe link in every
  email, and have a processor agreement with the email service.
- **Subscriptions.** Selling them brings in pre-contract information and 14-day
  cancellation rights (Consumer Contracts Regulations 2013), and whatever
  subscription rules of the Digital Markets, Competition and Consumers Act 2024
  are in force by then.
- **The page-view counter.** PECR (Schedule A1, in force 5 Feb 2026) lets it run
  without consent only with a free, simple way to object, which the site does
  not have yet (`COUNTER_TOKEN_ENV` in `scripts/build_site.py`).
- **Advertising rules.** A .uk domain or paid placement brings the site's own
  claims under the CAP Code: never "safe", and hold evidence for every factual
  claim (rules 3.7 and 3.9).

## Deploy the click-anywhere API (optional)

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
the map loads at the app's `.fly.dev` address. The API process needs about
1 GB; loading is serialised because two concurrent loads of the network
exceeded a 2 GB machine. The mistake to avoid is deploying
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
