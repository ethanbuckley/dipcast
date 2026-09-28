# dipcast

Probabilistic sewage-pollution risk for river and lake swim spots in England.

Live site: https://ethanbuckley.github.io/dipcast/ (forecasts for 88 named
spots, refreshed every 30 minutes by a scheduled GitHub Actions job; free to
run, never sleeps). Source: https://github.com/ethanbuckley/dipcast (MIT).
Every forecast issued is scored later and published on the site's
verification page.

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
what dipcast would have said for that day from reanalysis rainfall, and
compares it with the naive competitor, rainfall at the site in the previous
48 hours. Results (Spearman rank correlation with log E. coli; AUC for samples
over 900 cfu/100 ml, the inland "sufficient" threshold):

| Predictor | Pooled ρ | Within-site ρ | AUC > 900 |
|---|---|---|---|
| rainfall, previous 48 h at the site | 0.09 | 0.35 | 0.65 |
| dipcast spill risk (rain-driven spill model through transport) | 0.57 | 0.29 | 0.80 |

Two different questions hide in that table. *Which sites* are contaminated:
site-mean dipcast risk ranks the 32 sites' mean E. coli at ρ = 0.68, and
site-mean rainfall does not (−0.25). The transport layer, the part of dipcast
that is new, is what carries this. *Which days* are bad at a given site: on the
raw scale rain in the last 48 hours leads (0.35 against 0.29; on rivers 0.48
against 0.41), but a within-site comparison depends on the scale the site mean
is removed on. On the logit scale, the scale dipcast combines contributions on,
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

The honest reading: dipcast's forecast tells a swimmer how exposed a spot is
and when the overflows above it are likely to spill, and on rivers its
day-to-day signal is as good as recent rainfall; it does not yet capture the
diffuse runoff (farms, roads, urban drainage) that rain washes into rivers
regardless of overflows, and at inland bathing waters neither signal captures
most of the day-to-day variation. The next model should predict E. coli
exceedance directly from both, which these 2,165 samples make possible. Full
tables: `data/processed/ecoli_validation*.json|csv`.

Correction (14 Sep 2026): the first run of this validation (12 Sep) reported a
within-site ρ of 0.23 for dipcast. That run fed only the sample days into the
travel-time shift, which assumes consecutive days, so a fifth of each
overflow's contribution landed on the following week's sample; it also applied
the lead-4 rather than lead-0 Platt calibration. The hindcast now runs on a
continuous daily grid and the figures above are from the corrected run. The
observed-spill result was computed differently and did not change.

**Would forecast-led sampling catch more?** A question for anyone who pays for
water samples: if you could only afford half your sampling days, would choosing
them from the rain forecast catch more of the failures? `scripts/sampling_plan_test.py`
ranks each site's sampled days by rain in the previous 48 h and keeps the wettest
half. On rivers (947 sampled days, 202 exceedances of 900) that keeps 77% of the
exceedances with rain as it fell, 75% with the forecast issued that day, 68% with
the forecast issued two days earlier, and 68% four days earlier; a fixed schedule
keeps 50%. A "sample only if more than 5 mm is forecast" rule keeps a quarter of
the days and half the exceedances (43% exceedance rate on the days it picks, 14%
on those it skips). The skill is lost between same-day and one-day-ahead
decisions, not after, so a plan made four days out is as good as one made the day
before. It is the rain forecast doing this work, not the transport layer; on lakes
(16 exceedances) there is nothing to plan around. Archived forecasts by lead
come from `scripts/fetch_rain_leads_bathing.py`; results in
`data/processed/sampling_plan_test.json`. The test can only choose among days the
EA happened to sample, so it measures ranking skill, not the value of sampling on
days nobody did.

**E. coli exceedance model.** The map's "E. coli > 900" column: the estimated
probability that a midday sample exceeds 900 cfu/100 ml. `scripts/train_ecoli.py`
fits a logistic regression from things dipcast can compute anywhere: rain at the
spot in the previous 48 and 24 h, dipcast's overflow exposure for the day, lake or
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

**Live scoring rules for the spill forecasts (16-17 Sep 2026).** The forecast
scored for each overflow and day is the latest one issued by 08:00 local time on
the issue day, so "today" is the forecast a swimmer had at breakfast, not the
end-of-day estimate the earlier rule (latest issue of the day) produced. Issue
days with no forecast by 08:00 are missed deadlines: counted and listed on the
verification page, not scored. An overflow-day counts as "no spill" only if the
poller recorded that overflow with a known status at least six times that day,
from a feed whose freshest `LastUpdated` was under 6 h old, with no unobserved
stretch longer than 6 h (counting midnight to the first poll and the last poll
to midnight), and once the next day. `live_coverage.parquet` holds one row per
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
accumulates, and the page reports the same scores under a stricter 3 h gap rule
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
- The E. coli column is validated on river bathing waters in the May-September
  sampling season. On lakes it has no ranking skill and is not shown; outside
  the season it is an extrapolation and is marked as such.
- OS Open Rivers has small breaks at weirs, mills and culverts, and side
  channels (mill streams, leats) that are not connected upstream. dipcast joins
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
scorer, coverage accumulation in the poller and the build-health guard.

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
6. Dwr Cymru live status (no ArcGIS feed; would need their own map's API).

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
2025` reproduces the lead-time table; `scripts/validate_ecoli.py` then
`scripts/validate_ecoli_combined.py` reproduce the E. coli tables.

Every forecast is logged (coordinates, time, values; nothing about the user)
and scored once its days have passed, using the accumulated live polls. The
result is public at `/verification`, alongside the offline tests. `/terms` and
`/privacy` hold the plain-English terms and privacy notice.

## How the free site works

`spots.csv` lists the spots: the 38 Environment Agency designated inland
bathing waters and about 50 well-known river and lake spots. Add one by pull
request, or ask for one with the "Request a spot" issue template; it appears
in the next run. Inclusion is not a statement that a spot is safe.

`.github/workflows/site.yml` runs every 30 minutes and on every push. It
restores the mutable state (live polls, forecast log, rainfall cache) from
the Actions cache, or from the rolling `state` release if the cache is cold;
downloads the river network from the `data-v1` release (113 MB, too big for
git); runs `scripts/build_site.py`, which polls the nine live feeds, rebuilds
the overflow table, forecasts every spot with `forecast_point`, scores logged
forecasts against the accumulated polls, and writes `site/`; saves the state
back to the cache and, twice a day, to the release; and deploys `site/` to
GitHub Pages. Nothing is committed by the job except a monthly heartbeat, so
the repository does not grow.

The click-anywhere API (below) is the same code behind a FastAPI server. It
is what to run when someone needs forecasts for arbitrary points or an API,
and it costs about £8 a month on Fly.io; the static site costs nothing.

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
