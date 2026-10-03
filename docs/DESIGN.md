# SwimSignal design system

Written 1 October 2026, when the site was redrawn to look like one product rather than a set of
generated pages, and revised on 2 October 2026 for the fifth round, "a field guide, not a
dashboard", and the seventh, "the sky and the glass" (both below). This file is the reference for
anyone changing how the site looks: what was wrong, what was decided, and the rules that keep the
pages consistent. The stylesheet that carries the system is `src/dipcast/api/static/page.css`; the
app page, `src/dipcast/site/index.html`, repeats the tokens and the header inline so that it paints
before any stylesheet arrives and works offline on its own. A token changed in one must be changed
in the other: `tests/test_design_tokens.py` fails the build when the two sets differ, or when a rule
in either writes a colour, a font size or a radius instead of using a token.

## What was wrong

Seen on 1 October 2026, at 375 px and 1440 px, light and dark:

- **No typeface had been chosen.** Everything was the system font stack, so the site inherited
  whatever the device had. Headlines were 800 weight with tight tracking, under small uppercase
  tracked labels ("PLAN YOUR NEXT SWIM", "POLLUTION RISK", "SEWAGE SPILLS UPSTREAM"). That stack,
  eyebrow + heavy headline + muted lede, is the signature of generated interfaces.
- **Rounded, floating surfaces.** Cards at 16 px radius with two-layer drop shadows, pill buttons
  at 999 px, a gradient-tinted intro card, hero cards washed in the level's pastel. Eight different
  corner radii were in use (8, 10, 11, 12, 14, 16, 20 and 999 px).
- **Two sites.** The app had a teal bar, cards and a grey page; the prose pages (About, Accuracy,
  Terms, Privacy, Testing, Feedback) had a white page, a plain text nav, smaller type and their own
  heading sizes. Their navs disagreed on names ("Map" and "Explore" for the same page) and the
  page titles put the brand on different sides ("SwimSignal · About", "Feedback · SwimSignal").
- **The accuracy page read as a notebook dump:** a callout box, then a wall of small grey
  paragraphs and bare tables in a monospaced font, with no way to see the four numbers that matter.
- **Text glyphs as icons:** ★ and ☆ on the Save button, ← on back links, + and – on folds, ✓ on
  the swim log, and a 64 px "404".
- **Caveats repeated** under every control, so the honest ones were lost among the decorative ones.

## Decisions

### Type

Two typefaces, both by Adobe under the SIL Open Font License, served from this site (`fonts/`, with
the licence beside them) so that no third party receives a request:

- **Source Serif 4** for the wordmark, page headings and spot names. A serif at
  600 weight, never heavier. Variable weight and optical size, so it is sturdy at 18 px and fine at
  34 px.
- **Source Sans 3** for everything else: body, labels, controls, tables. Weights 400 and 600, and
  700 for headlines and level words; 300 (Light) for a tile's figure (seventh round). The file
  carries weights 200 to 900, and both stylesheets declare all of them.
  Risk levels, operational card headings and numerical results use the sans: they need to read
  quickly, while the serif gives places and reports their character. Tables and headline figures
  explicitly use tabular numerals.
- The monospaced stack is kept for one thing: model version strings.

Six sizes, in pixels, as tokens (`--fs-*`) in both stylesheets, and no other size anywhere: 13
meta (used sparingly: a row's kind, the week's letters, a tile's label, the foot), 15 notes (notes,
controls, a row's headline, a tile's sentence), 17 body (the app and the prose pages alike), 20
section headings (and the wordmark, a prose page's lede and h2, a saved spot's level), 30 a figure
(a tile's, a headline without a level), 36 the spot's name, page titles and the answer's level, 30
on phones (800 px wide or less, where the app's phone layout begins, and the same step on the prose
pages; until 2 October the prose pages dropped it at 540 px). A prose page's h3 is the body size in the
serif. Line height 1.5 for text (both files; `page.css` had 1.55), 1.1 to 1.25 for headings. No
letter-spacing except −0.01em on the 36 px headings (`--fs-title`: page titles, the spot's name, the
answer's level, the Accuracy figures), and no uppercase labels anywhere. (Until 2 October the
wordmark and every h1 to h3 had −0.005em and the 30 px figures −0.01em, below what the eye can see at
those sizes; they were taken off to keep the rule simple.)

### Colour

- A warm paper page `#f6f4ee`, one white surface `#fff`, ink `#1b2328`, muted `#5a6166` (5.7:1 on
  the paper), hairlines `#e0dbd0`. Light only (fifth round): nothing is hard-coded in a rule.
- Brand teal `#0f5a61` (the icon's), the one accent: the mark, links, the primary button, a chosen
  filter, focus rings. The low-risk filter uses the moss text shade `#326a43` with white text.
  White on a fill (the bar, the brand, a chosen control) is `--on-fill`; the veil over the map
  that says "two fingers to move" is `--scrim`.
- The mark in every header is the icon's teal too. It was a lighter `#1a6871` from 1 October,
  chosen to stand off the teal bar of the time; on the slate bar both shades are about 1.1:1 to it
  (`#1a6871` 1.14, `#0f5a61` 1.07), so the lighter one no longer bought anything and the two marks
  were unified on 2 October. The mark reads by its river, dot and ring, not by its square.
- `--faint` (`#676b6e`, the week's letters, a placeholder) is 4.9:1 on the paper and 5.4:1 on
  white, but 4.25:1 on the bare sky, under AA. It is for tiles, fields and the paper only: checked on
  2 October at 375 and 1440 px, every piece of faint text on the list, a spot and the Saved page
  sits on a tile.
- The header is a slate bar, `#3d5b5d` (`--bar`, seventh round), a darker shade of the picture's
  nearest fells: white on it is 7.4:1, and the links' 86% white (`--bar-ink`) 5.6:1.
- The four levels keep their meaning and are the only strong colours on a page, in one natural
  palette: moss, ochre, rust and brick. Marks (map, day strips, bars, rules): low `#3b7d4f`,
  moderate `#b7791f`, high `#c2552a`, very high `#a32d2d`. Text (headlines, values): `#326a43`,
  `#855817`, `#a04623`, `#992a2a`, which pass 4.5:1 on the paper and on white (5.6:1 or more on the
  paper). The ochre and rust marks are too light for text (3.3:1 and 4.1:1 on the paper), hence
  the two sets. A spot with no monitored overflow upstream is teal `#4aa39a`, not grey: it is a
  calm answer, not a missing one. Grey `#98a2aa` means no level. An overflow discharging now is
  the very-high brick.
- The map: OpenStreetMap's tiles with the land in grey and the water (sea, lakes, rivers) in a
  muted blue, `#bccfd8`, about as light as the grey it replaced (L* 82 against 83; chroma 8). Its
  hue (235°) is 48° from the teal marker's, which keeps a paper ring, and the open spot's dark
  teal ring has 4.9:1 against it. CSS filters change every colour alike, so the tiles go through
  an SVG filter in `index.html` (`#tiles`) that finds OpenStreetMap's water by its colour. That
  blue is not a token: the filter's numbers cannot read one.
- A level colours the answer's big word, a day's bar and the dot on a scale. The written level
  carries the meaning even when colour is hard to see; the marks are a supporting cue. It never fills
  a surface: the washed-tint card was the dashboard look, and five of them in a column were a wall
  of pastel. A level word in a table or the swim log is the word in its text colour, not a filled
  label.

### Shape and surface

- One corner radius, 8 px (`--radius`), for the hero, inputs, buttons, a chosen chip, the maps,
  and the controls, tooltips and credit on the map. Count badges and dots are round, because they
  are circles. Nothing else is a pill. Two small radii are tokens of their own and used for nothing
  else: the mark's 6 px corner (`--radius-mark`) and the 4 px of a focus ring round a text link
  (`--radius-ring`). A header link's hover, the map's credit, its tooltips and its "Map" button had
  6 px until 2 October; they are 8 now. Bars are square, on the Accuracy page as in the app.
- The answer has no box (seventh round): its words sit on the sky. Everything after it is a tile,
  frosted where the picture is behind it and near white on the paper below, with no border; inside
  a tile, hairlines. No shadows and no gradients, except a 1 px lift under the controls that sit on
  the map, which need to read against tiles. Notices (stale, offline) keep their box: they are
  exceptions to read first.
- The five days are rows, as Apple's ten days: the day, its level, and a bar of four bands filled
  into the day's band. The open day is shown by a shaded row with a rule at its left, not a glow.
- The forecast issue time is the answer's last line, before the five days, so freshness is
  visible at the point of deciding.

### Words

- Labels are sentences or phrases in normal case: "Sewage spills upstream", "Water quality",
  "Saved spots", "All spots".
- A level says "risk" wherever it heads a line (seventh round): "Very high risk today: sewage
  spills", "Low risk by Tuesday", "High risk" in the answer and on a saved card, the map's
  tooltips, a picked day's rows. A bare "Very high" read as very high what. The word stands alone
  only under a heading that names it: the five days ("Pollution risk, next five days"), the
  day-by-day table's "Risk" column, the map's key.
- One caveat per view, in the place it is read: the intro says "Forecasts, not water tests" once;
  the hero's last line says to check the signs at the water. The rest of the explanation lives
  under "About these forecasts" and on the About and Accuracy pages.
- A row says one sentence and folds the rest (fifth round): the hero's rows keep the sentence with
  the figures in view and put the rest under a fold named for its tile, "What <label> means"
  (one name per fold, so a screen reader's list of them tells them apart; until 2 October every
  one was "What this means"), word for word. What the strip's
  cells show folds into the water row, or the spills row where there is no water estimate; under
  the strip only the † sentence stays, and only while a cell carries a †. The EA rating keeps its
  advice in view and folds how it is rated and the years before. A section's note under the answer
  does the same: River level keeps "Not part of the pollution level." in view, and the definition
  of reach under the overflows folds whole.
- "About these forecasts" is in full on the list only. A spot's page and the Saved page give it one
  line, "About these forecasts · How accurate is it?", which opens it in place; so does any link to
  a part of it (the levels, what E. coli counts, your data).
- Kept in view, once per view, whatever else folds: "A forecast, not a water test: check the signs
  at the water before you swim", every Environment Agency advice sentence, the issue time, the
  data credits in the foot, and every sentence of the terms and the privacy notice. Consent text
  (the alerts) folds under what it is about, before the button, and keeps every sentence.
- Names agree everywhere: the home page is **Explore**, the verification page is **Accuracy**.
  Page titles are "Page · SwimSignal", where the page's name leaves out the brand its heading may
  carry: "About SwimSignal" is "About · SwimSignal", "Testing SwimSignal this winter" is "Testing
  this winter · SwimSignal". A spot's page is "Name: pollution risk forecast · SwimSignal". One
  exception, kept for search: the home page is "SwimSignal · pollution risk forecasts for swim
  spots", so a search result starts with the name.
- One name for each thing (2 October 2026). Before this the product went by four descriptions, the
  E. coli figure by six names and the spill score by four.
  - The product is a **pollution risk forecast**, in titles, descriptions, the manifest, the foot
    and the About page. Not "sewage-spill forecast" or "sewage-overflow risk": the level also
    takes in water quality and the bathing-water rating, and the strip already says "Pollution
    risk". "Sewage" stays where a sentence is about the overflows themselves.
  - The spill score is the **exposure index**, defined where it is explained (About, Accuracy,
    Testing) as "a 0–100 score of how likely sewage from upstream is to reach the spot". Not
    "spill risk", "overflow exposure" or "the modelled chance", and not called a probability.
    Its tile and rows keep their labels, "Sewage spills" and "Sewage spills upstream".
  - The E. coli figure is the **E. coli estimate** in prose, and its tile is **Water quality**.
    Not "E. coli chance", "E. coli risk", "E. coli > 900" or "water-quality estimate".
  - **Dŵr Cymru Welsh Water**, with the circumflex, in prose.
- Abbreviations are spelled out where a reader meets them: "Environment Agency" in full before
  "EA" on any page; "WwTW", "STW", "CSO" and "SPS" in overflow names are explained on the About
  page; "lead calibration" is defined under the live table on Accuracy. "cfu" is not used: the
  threshold is "900 E. coli per 100 ml".
- A part that cannot be used yet is not shown: Compare appears on the Saved page once two spots
  are saved. A picked day's details begin with its rows, because the hero's headline already
  gives that day's level and why.

### Icons

One stroke set, 1.75 px, round caps: search, map, bookmark (Save and Saved), chevron (back links
and folds), check (swim log), and since the seventh round one for each tile's label (`ICON`: the
days, spills, water, right now, river level, rain, sun, rating, algae, map, nearby, the table).
The chevrons drawn in CSS (back links, folds, a tile's corner) and a select's arrow (`--arrow`) use
the same 1.75 px. The search icon (`SEARCH_ICON`) was still 2.2 on 2 October.
Inline SVG, so they inherit `currentColor` and need no file. The brand mark is inlined in every
header for the same reason: it needs no path to resolve at any depth.

From the fifth round to the seventh the one decorative element was the river line, the mark's
river turned to run across, under the list's heading and on the empty states. Ethan had it taken
off the pages on 2 October 2026: the picture of the fells is the motif now. The link-preview card
(`icons/og.png`, drawn by `scripts/make_share_image.py`) still draws it under the name, on the
paper, as the site's first screen looked in the fifth round.

### Layout

- **Header**, shared by every page: the slate bar (seventh round), the mark, the serif wordmark and
  the main links (Explore, Accuracy, About, Feedback; the app adds Saved) in white. The current page
  is underlined in white.
- **App, desktop:** map left, a 460 px column right. **App, phone:** list first, the map one tap
  away in the bar at the bottom, a spot's page with its own small map. Unchanged.
- **Prose pages:** one 720 px column, serif headings, 17 px text, a summary box where a page has an
  answer in a few lines, and the shared foot: one line about what the site is, then the small links.
- **Accuracy page:** the four numbers that matter in a ruled definition list at the top, a short "in short" list,
  a contents list, then sections in the same order as before. Tables share one style; the three
  reliability tables draw forecast against observed as bars. A table wider than the screen scrolls
  sideways in its own box, between two hairlines, and the bar column keeps at least 120 px. The
  scoring rules fold away under "How the live scoring works", so the page opens on results.

#### Head of every page

Each prose page's `<head>` carries, in this order:

- the title, "Page · SwimSignal", and a meta description (Terms and Privacy have none);
- `theme-color`, the header's slate, `#3d5b5d`, so the browser's own bar runs on from it (one
  value: the site is light only);
- the icon, `icons/icon.svg`, and the Home Screen icon, `icons/apple-touch-icon.png`;
- the stylesheet, `page.css`;
- preloads for the two faces every page uses, `SourceSans3-latin.woff2` and
  `SourceSerif4-latin.woff2`, so that text swaps into them sooner. The italic is rarely used and
  is not preloaded.

The pages are written for the API server, so these links start `/static/`; `scripts/build_site.py`
rewrites each one for the static site (`REWRITES`). The API server has its own copies of the two
icons in `src/dipcast/api/static/icons/`, taken from the site's `src/dipcast/site/icons/`, and a
test checks that they still match. The 404 page, which the build writes, has the same head with
absolute links, `noindex` and no description. The app page's head is its own, in `index.html`: it
adds the manifest and the Home Screen tags. The Home Screen app's status bar is
`black-translucent`: the page draws under it, its white text sits on the slate, and the header's
top padding takes in the safe area. (From the fifth round to the seventh it was `default`, dark
text, over the paper header.) Nothing in any head is fetched from another site: the app's map
library is this site's own (below). The API server's `/` sends a visitor to the site
(`DIPCAST_SITE_URL`, else swimsignal.co.uk); its own map page, `static/index.html`, which had the
system font, uppercase pill badges and a palette of its own, was removed on 2 October.

#### The map library and the offline copy

Leaflet 1.9.4 is served from this site, `vendor/leaflet/` (from `src/dipcast/site/vendor/leaflet/`,
which the build copies), with its BSD 2-Clause licence and a `VERSION.txt` beside it, for the same
reason as the fonts: no third party receives a request when a page opens. Until 2 October it came
from unpkg.com, which the privacy notice then had to list. The page still pins both files by hash
(`integrity`), and a test checks the hashes against the files; an upgrade replaces the files and
the two hashes together.

The offline copy (`sw.js`) stores only this site's files. Its cache is named `dipcast-<stamp>`,
where the stamp is a hash of the files it stores or that decide what it stores (`SHELL_SOURCES` in
`scripts/build_site.py`: the page, the worker, the scripts, the stylesheet, the icons, the picture,
the fonts, `vendor/`). The build writes the stamp into the worker, so a changed file changes
`sw.js`, browsers install the new worker, and it fills a new cache from the server (`no-cache`),
deleting the old one when it takes over. A hash of the files, not the commit, so a commit that
touches neither (the model, the tests) does not make every visitor download the shell again. The
page asks for `levels.js` and `experience.js` at `?v=<stamp>`, so a page and its level rules always
come from one build, even when one of them loses the 4 s race to the network. A file added to the
worker's `SHELL` belongs in `SHELL_SOURCES` too. The `dipcast-` prefix stays: the worker and the
page's "Turn off the offline copy" clear caches by it.

### What a swimmer wants first (second round, 1 October 2026)

The question a swimmer brings is one of three: is my spot all right today or this weekend; where
near me is low on Saturday; why is it high, and when does it ease. So the home page opens on the
answer. First the heading, one line and the search. Then one row of controls, the day picker and
Low risk, with one muted line under it holding the caveat ("Forecasts, not water tests.") and the
week's best day. Then the saved spots, or one line on how to save one. The kinds of water (rivers,
lakes, bathing waters) are a second row under All spots, because they narrow that list only. The
list carries no colour key: each row says its level in words, and the bars are explained once at
the foot of the list. On a 375 px phone the first saved spot is now above the fold; before, the
first screen was controls alone.

On a spot's page the issue time and today's weather share one muted line between the answer and
the five days, so freshness is read at the point of deciding. While the forecast loads, the home
page shows the list's shape in hairline grey rather than a line of text. Leaflet's own controls
(zoom, attribution, popups, tooltips) use the tokens, the one radius and the map lift, so the map
no longer carries a second visual language. A control keeps its own corner when focused. Fold
summaries and stand-alone text buttons are 44 px targets. (Since 2 October.) Every map's credit is
OpenStreetMap's alone; the terms credit Leaflet. On a map too narrow for the legend and the credit
side by side, the legend sits above the credit's line. A phone's Upstream map shows neither the
legend nor the list's controls, which covered the ringed spot.

### Where instead (third round, 1 October 2026)

A spot that reads moderate or worse on the day shown, or is rated poor, carries a "Lower risk
nearby" card after its answer: up to three spots within 40 km that are lower that day, nearest
first, each opening on the same day, with one line under them saying a lower level is not clean
water. A spot without a level is never offered, and nor is a water rated poor, because advice
against bathing applies there whatever the level of the spot beside it. In the hero's rows the
sentence with the figures stays in view and the explanation folds under "What <label> means"; the
EA advice and "A forecast, not a water test" stay visible. The list's counts begin with the issue
time. On the Saved page the cards come first and Compare below them. A redrawn view rises 4 px
into place over 0.22 s, as a picked day does, and not at all for anyone who asked for less motion.
The desktop legend is the title, the six levels in two wrapping lines and the overflows' key: about
half its old height. A spot's four actions are a two-by-two grid. The brand link is a 44 px target
(padding inside a negative margin, so nothing moves) and the mark's corner is 6 px, the small
radius.

### The way back (fourth round, 1 October 2026)

Back returns to the place it left. The list and the Saved page keep their scroll, and the row that
had the focus, whenever they are left (for a spot, for each other, by Back or Forward) or covered
by the full map, and get both back once on the way back: a reader at row 60 of 89 is not sent to
row 1, and the next Tab carries on from the row they opened. Explore and a fresh load still start
at the top. On a phone, a spot opened from a marker on the full map goes back to the map at the
view it had; its back link reads "Map", and closing the map forgets it. Every spot with a forecast
has a "Nearby" card: the three nearest spots within 40 km, whatever their level, each row giving
its headline. A spot that reads moderate or worse that day, or is rated poor, keeps "Lower risk
nearby", with only lower spots and the line that a lower level is not clean water. On a desktop,
"/" puts the focus in the search and Escape empties it.

Two wording rules came out of this round. A level word stands beside the thing at risk, never
beside the thing measured: the water-quality row says "Very high risk", and a list row "E. coli
risk 63%", because beside "Water quality" a bare "Very high" read as very good water. The
Environment Agency's classification words (excellent, good, sufficient, poor) are used only for
the Agency's own rating. And a button says what happens: the feedback form's button is "Send by
email", which opens the reader's email app with the message, with the copy of the text beneath
as the fallback; it was "Prepare email" followed by a second step.

### A field guide, not a dashboard (fifth round, 2 October 2026)

After four rounds the site still read as generated, and the reasons were specific (they are listed
in `docs/DESIGN-HANDOFF.md`): every block a bordered white card in a column, a saturated teal bar
the loudest thing on every page, a page grey so close to white that only borders separated
anything, seventeen type sizes, a Leaflet-demo map, and no motif. So:

- **One white surface per page.** The hero is the only card. Right now, River level, Nearby,
  Overflows, Day by day, the Saved page's sections and the About section are unboxed: a hairline,
  a serif heading, the content. The list and the Nearby rows lost their container and kept their
  dividers. The Saved page's spots are entries between hairlines, the name in the serif, then the
  headline and the strip. A list someone shared keeps a box while it is on offer.
- **A warm paper page** (`#f6f4ee`), so the white hero lifts without a border and the map reads
  as a different material.
- **A light header**: the paper, the mark and the wordmark in teal, ink links, a hairline. The
  phone's bar at the bottom is unchanged.
- **Six type sizes** (13, 15, 17, 20, 30, 36) as tokens, the spot's name and page titles at 36 (30
  on phones), the headline level at 30, then a real drop to the 17 px body. 40 px from the answer
  to the first section, 32 between sections, 8 and 12 within them.
- **Natural level colours**, moss, ochre, rust and brick, with darker text shades that pass AA on
  the paper and on white.
- **A grey map, then blue water**: the tiles in greyscale, so the markers are the only strong
  colours on it; markers a size larger with a 2 px paper ring; the legend the six keys in one
  line, with the overflows' key below it only while overflows are on the map. Still
  OpenStreetMap, which the privacy notice names. The same day the water came back as a muted blue
  (Colour, above): grey, the lakes had matched the woods and the fells around them, and at street
  level Derwent Water could hardly be told from the land. Faded OpenStreetMap colours were tried
  and dropped, because they bring back the green parks and the coloured roads that competed with
  the markers; so was a paper-coloured land, which turns the fells brown.
- **The river line** from the mark as the one decorative element.
- **Quieter controls**: the chips and the sort control lost their borders (words in the text's
  weight, the chosen one filled); the day strip lost its outer box.
- **Light only.** The dark theme was the light one with inverted tokens, not a design; the site is
  read outdoors, where the light theme's contrast is what counts; and keeping two palettes in step
  had cost each round effort. A dark theme, if wanted later, is a deliberate toggle with its own
  palette.

### The sky and the glass (seventh round, 2 October 2026)

Ethan asked for the cleanness of Apple Weather, and chose the closest of four mock-ups (a paper
version, Apple's order without boxes, Apple's tiles on paper, and this) over the recommended one.
What makes Apple's page clean, and what was taken from it:

- **One answer, first.** The level and "risk" at one size, the title's (36 px, 30 on phones), in
  semibold sans and the level's colour: "High risk", since a bare "High" read as high what. Under
  it, what set it and when ("Sewage spills today"), where the five days go ("Low by Tuesday",
  Apple's high and low) and the issue time. Centred, with no box. The first build set the level
  alone at 72 px Light with a small "risk" beside it, as Apple sets "19°"; Ethan found it goofy and
  too large, and picked this from three settings (Regular at 44, this, Bold at 30).
- **A sentence over the days**, as Apple's over its hours, saying something the headline does not:
  "About 13 of the 60 overflows upstream are expected to spill today, fewer each day after."
- **Data drawn.** The five days are rows with a bar of four bands, filled into the day's band and
  placed in it by what set the level (the exposure index, the E. coli estimate where it counts, the
  foot of high for a water rated poor), so the bar always agrees with the word beside it. Drawn
  from the spills alone, a water rated poor showed "High" beside an almost empty bar. The tiles draw
  the exposure index and the E. coli estimate on stepped scales, the river's level on its usual range, every
  overflow upstream as a dot, the rain as columns and the sun's path over the day.
- **One anatomy.** Every tile is a label with its icon, one figure, one drawing, one sentence, and
  the rest of the explanation behind a chevron in its corner: Sewage spills, Water quality, Right
  now, River level, Rain, Weather, the EA rating and the algae where they apply, then Nearby, the
  Upstream map, the overflows that matter most and the day-by-day numbers.
- **A background that is the place.** A picture of fells over a lake (`icons/fells.webp`, 8 KB,
  drawn by `scripts/make_sky_image.py`, so it needs no licence and no request to anyone else) hangs
  behind the opening of each view: its sky ends at the bottom of the answer's words, its fells fill a
  128 px band below them, and it is the paper about 300 px further down. Its top rows are the
  `--sky` colour that runs on up to the header, so it has no edge. The weather data has only the
  day's high, sunrise and sunset, so the picture cannot follow the weather, as Apple's does. A
  photograph would be a separate job, with sourcing and licences. The picture is 500 CSS px wide; from
  2 October a view wider than that (a screen 501 to 800 px wide: a phone on its side, a small tablet) stretches it
  sideways to both edges at the same 600 px height, where before its sides showed as hard vertical edges.
- **Glass, light.** The tiles are frosted (`--glass`, a blur and 76% white). Measured on 2 October
  2026 over the darkest fell: the grey text and the level colours pass 4.5:1 at 76% and fail at
  64% (4.1 to 4.2), so the panes are three-quarters white and the picture shows through them only
  faintly. On the built pages (8,492 text runs, nine views at 320, 375 and 1440 px) every one passes
  AA, against the darkest pixel behind it: the closest are 4.70:1 on the sky and 4.82:1 on the
  glass. Nothing below the
  answer's words sits on the picture without a tile: on the list the saved spots, the hint and all
  the spots are tiles, and so is the empty Saved page. Plain white where a browser draws no blur, or
  the reader has asked for less transparency.
- **Saved as Apple's places**: each spot's name, its level with "risk" (20 px bold), what set it
  and where the week goes, and the week as small bars. The list shows the same cards on one tile.
- **A slate bar on top.** Shown the old teal bar, a sky with no bar and a slate bar beside the
  paper header, Ethan chose the slate: a darker shade of the picture's nearest fells, so it frames
  the sky and echoes the fells under the answer, where the old teal swallowed the mark's teal
  square and competed with the level colours. The river line came off the pages at the same time
  (Icons).

What it reverses, knowingly: one white surface per page (round five) becomes tiles, as Apple's page
is; the level's headline grows from 30 px bold to the title's size, semibold; the light header
(round five) becomes a dark bar again, slate rather than teal. Uppercase labels, borders, shadows
and gradients stay out: a tile's label is normal case, and a bar's bands are steps. Overflow names
that arrive in capitals ("LITTLE SALKELD WwTW") are set in normal case (`nameCase`). The daily E.
coli figures left the five days for the water tile (today and tomorrow) and the day-by-day table.
The prose pages keep the paper.

### Swimmers' reviews (3 October 2026)

Reviews (`reviews.js`; the service is `reviews/`) are one more tile with the same anatomy, after the
others and before the Upstream map and the overflows, so the forecast still comes first. Its figure
is the share who would swim there again, "75% would swim here again", shown from three reviews
(fewer would make a percentage of one or two people), drawn as a bar in ink: a score is not a level,
so it never takes a level's colour, and no tomato, star or thumb stands in for the words. Under the
figure, one sentence ("3 of 4 swimmers would swim here again"), then the reviews between hairlines,
newest swim first: the yes or no in bold as the row's headline, then who and when ("Priya · swam 21
Sept 2026"), the text, photos as 72 px squares that open full size over the page, and Report (Delete
on your own review) in the meta size. The first three show; "Show all" opens the rest. The score is
also a link at the end of the line over the spot's name, the place's own facts, in the meta size,
never in the answer. Writing one opens a form in place of the button: a question to each field, in
the order a swimmer answers them, the yes or no as two buttons that fill when chosen, as a chosen
chip does.

### What was kept on purpose

The information architecture (list → spot → day), every word of the terms and privacy notice, the
accessibility work (focus rings, `aria-pressed`, 16 px inputs so iOS does not zoom, 44 px targets),
the phone's bottom bar, offline behaviour, and the level rules in `levels.js`. The redesign changed
how things look, not what the site says.

## Rules for changes

1. Add a colour, size or radius only as a token in `page.css`, and mirror it in `index.html`. A
   font size is one of the six `--fs-*` tokens, or it is a seventh size. The two sets must stay
   equal (`tests/test_design_tokens.py`); only the app's layout and filter tokens (`--tile-filter`,
   `--header-h`, `--nav-h`) live in `index.html` alone.
2. Place names, page titles and section headings in the serif, 600; risk levels, a row's headline,
   controls and numbers in the sans. No uppercase labels, no tracking.
3. No new radius, shadow or gradient. In the app the answer is unboxed and everything after it is a
   tile (seventh round); within a tile, and on the prose pages, separate with a hairline and
   whitespace, not a box. No words on the picture below the answer's own without a tile under them.
4. Icons are inline SVG from the one stroke set; never a text glyph.
5. A level may colour text, a day's bar and a dot on a scale. It may not fill a surface.
6. Check a change at 320, 375 and 1440 px before opening a pull request. The local preview is
   `.claude/launch.json` (`python3 -m http.server 8766 --directory site`) after
   `scripts/build_site.py` has written `site/`, or after writing the pages alone with
   `build_site.write_pages` over a downloaded `site/data/spots.json`.
7. In `page.css`, the prose defaults for paragraph and list spacing are written as
   `:where(main.doc) p`, with no specificity, so that a component's class sets its own spacing.
   Written as `main.doc p`, a default outranks a single class such as `.note`, and the
   component's spacing is silently lost.
8. Nothing is fetched from another site but the map's tiles (and, when it is on, the page-view
   counter). A library is copied into `src/dipcast/site/vendor/` with its licence and pinned by
   hash.

## Review refinements

The PR review kept the editorial identity but made the decision easier to scan: stronger sans-serif
risk headlines, readable day cells with aligned levels, plain place metadata instead of decorative
chips, a visible issue time, and 44 px controls. Secondary text and keyboard focus use shades that
stay legible on the page. The accuracy figures sit on the page between rules, rather than in four
more cards; their numbers use tabular sans-serif digits. Reliability bars draw the forecast in the link shade and the
observed in the muted grey (until 2 October the observed bar was the high level's rust, a level colour on something that is not a
level; then briefly ink, which beside the teal read as two near-black strips). Teal and grey differ in hue more than
in lightness (1.26:1; ink was 2.02:1), so the order, forecast above observed, and the key carry the difference too;
the grey is 4.45:1 on the empty track. On phones the prose header
gives all four navigation links a single full-width row.
