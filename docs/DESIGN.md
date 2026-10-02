# SwimSignal design system

Written 1 October 2026, when the site was redrawn to look like one product rather than a set of
generated pages, and revised on 2 October 2026 for the fifth round, "a field guide, not a
dashboard" (below). This file is the reference for anyone changing how the site looks: what was
wrong, what was decided, and the rules that keep the pages consistent. The stylesheet that carries the
system is `src/dipcast/api/static/page.css`; the app page, `src/dipcast/site/index.html`, repeats
the tokens and the header inline so that it paints before any stylesheet arrives and works offline
on its own. A token changed in one must be changed in the other.

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
  700 for risk headlines and level words. Risk levels, operational card headings and numerical
  results use the sans: they need to read quickly, while the serif gives places and reports their
  character. Tables and headline figures explicitly use tabular numerals.
- The monospaced stack is kept for one thing: model version strings.

Six sizes, in pixels, as tokens (`--fs-*`) in both stylesheets, and no other size anywhere:
13 meta (used sparingly: a row's kind, the week's letters, the day cells, the foot), 15 notes
(notes, controls, a row's headline, a factor's explanation), 17 body (the app and the prose pages
alike), 20 section headings (and the wordmark, a prose page's lede and h2), 30 the headline level,
36 the spot's name and page titles, 30 on phones. A prose page's h3 is the body size in the serif.
Line height 1.5 for text, 1.1 to 1.25 for headings. No letter-spacing beyond −0.01em on the
largest headings, and no uppercase labels anywhere.

### Colour

- A warm paper page `#f6f4ee`, one white surface `#fff`, ink `#1b2328`, muted `#5a6166` (5.7:1 on
  the paper), hairlines `#e0dbd0`. Light only (fifth round): nothing is hard-coded in a rule.
- Brand teal `#0f5a61` (the icon's), the one accent: the mark and the wordmark in the header,
  links, the primary button, a chosen filter, focus rings. The low-risk filter uses the moss text
  shade `#326a43` with white text.
- The four levels keep their meaning and are the only strong colours on a page, in one natural
  palette: moss, ochre, rust and brick. Marks (map, day strips, bars, rules): low `#3b7d4f`,
  moderate `#b7791f`, high `#c2552a`, very high `#a32d2d`. Text (headlines, values): `#326a43`,
  `#855817`, `#a04623`, `#992a2a`, which pass 4.5:1 on the paper and on white (5.6:1 or more on the
  paper). The ochre and rust marks are too light for text (3.3:1 and 4.1:1 on the paper), hence
  the two sets. A spot with no monitored overflow upstream is teal `#4aa39a`, not grey: it is a
  calm answer, not a missing one. Grey `#98a2aa` means no level. An overflow discharging now is
  the very-high brick.
- A level colours the headline and a 6 px rule on the hero. The written level in bold sans-serif
  carries the meaning even when colour is hard to see; the rule is a supporting cue. It never fills
  a surface: the washed-tint card was the dashboard look, and five of them in a column were a wall
  of pastel. A level word in a table or the swim log is the word in its text colour, not a filled
  label.

### Shape and surface

- One corner radius, 8 px, for the hero, inputs, buttons, a chosen chip and the maps. Count badges
  and dots are round, because they are circles. Nothing else is a pill.
- One white surface per page (fifth round): the hero, the answer, lifted off the paper by its
  colour alone, with no border. Everything else sits on the paper: a section is a hairline, a serif
  heading and its content. No shadows and no gradients, except a 1 px lift under the controls that
  sit on the map, which need to read against tiles. Notices (stale, offline) keep their box: they
  are exceptions to read first.
- The five days are one strip of five cells divided by hairlines, each with its level as a 4 px
  bar at the top, and no box round them. The open day is shown by an inset ring, not a glow.
- The hero's forecast issue time sits beside the answer, before the five days, so freshness is
  visible at the point of deciding.

### Words

- Labels are sentences or phrases in normal case: "Sewage spills upstream", "Water quality",
  "Saved spots", "All spots".
- One caveat per view, in the place it is read: the intro says "Forecasts, not water tests" once;
  the hero's last line says to check the signs at the water. The rest of the explanation lives
  under "About these forecasts" and on the About and Accuracy pages.
- Names agree everywhere: the home page is **Explore**, the verification page is **Accuracy**.
  Page titles are "Page · SwimSignal".

### Icons

One stroke set, 1.75 px, round caps: search, map, bookmark (Save and Saved), chevron (back links
and folds), check (swim log). Inline SVG, so they inherit `currentColor` and need no file. The brand
mark is inlined in every header for the same reason: it needs no path to resolve at any depth.

One decorative element, the river line: the mark's river, turned to run across, 120 by 20 px in a
1.75 px teal stroke (`RIVER` in the page script). It sits under the list's heading and on the empty
states (no saved spots, no spot matching a search), and nowhere else.

### Layout

- **Header**, shared by every page: on the paper, the mark and the serif wordmark in teal, the
  main links (Explore, Accuracy, About, Feedback; the app adds Saved) in ink, and a hairline below.
  The current page is underlined in teal.
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
- `theme-color`, the header's paper, `#f6f4ee`, so the browser's own bar runs on from it (one
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
adds the manifest and the Home Screen tags. The Home Screen app's status bar is `default` (dark
text): `black-translucent` would put white text over the paper header.

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
summaries and stand-alone text buttons are 44 px targets.

### Where instead (third round, 1 October 2026)

A spot that reads moderate or worse on the day shown, or is rated poor, carries a "Lower risk
nearby" card after its answer: up to three spots within 40 km that are lower that day, nearest
first, each opening on the same day, with one line under them saying a lower level is not clean
water. A spot without a level is never offered, and nor is a water rated poor, because advice
against bathing applies there whatever the level of the spot beside it. In the hero's rows the
sentence with the figures stays in view and the explanation folds under "What this means"; the
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
- **A warm paper page** (`#f6f4ee`), so the white hero lifts without a border and the grey map
  reads as a different material.
- **A light header**: the paper, the mark and the wordmark in teal, ink links, a hairline. The
  phone's bar at the bottom is unchanged.
- **Six type sizes** (13, 15, 17, 20, 30, 36) as tokens, the spot's name and page titles at 36 (30
  on phones), the headline level at 30, then a real drop to the 17 px body. 40 px from the answer
  to the first section, 32 between sections, 8 and 12 within them.
- **Natural level colours**, moss, ochre, rust and brick, with darker text shades that pass AA on
  the paper and on white.
- **A grey map**: the tiles in greyscale, so the markers are the only colour on it; markers a size
  larger with a 2 px paper ring; the legend the six keys in one line, with the overflows' key
  below it only while overflows are on the map. Still OpenStreetMap, which the privacy notice
  names.
- **The river line** from the mark as the one decorative element.
- **Quieter controls**: the chips and the sort control lost their borders (words in the text's
  weight, the chosen one filled); the day strip lost its outer box.
- **Light only.** The dark theme was the light one with inverted tokens, not a design; the site is
  read outdoors, where the light theme's contrast is what counts; and keeping two palettes in step
  had cost each round effort. A dark theme, if wanted later, is a deliberate toggle with its own
  palette.

### What was kept on purpose

The information architecture (list → spot → day), every word of the terms and privacy notice, the
accessibility work (focus rings, `aria-pressed`, 16 px inputs so iOS does not zoom, 44 px targets),
the phone's bottom bar, offline behaviour, and the level rules in `levels.js`. The redesign changed
how things look, not what the site says.

## Rules for changes

1. Add a colour, size or radius only as a token in `page.css`, and mirror it in `index.html`. A
   font size is one of the six `--fs-*` tokens, or it is a seventh size.
2. Place names, page titles and section headings in the serif, 600; risk levels, a row's headline,
   controls and numbers in the sans. No uppercase labels, no tracking.
3. No new radius, shadow or gradient. One white surface per page; if anything else needs
   separating, use a hairline and whitespace, not a box.
4. Icons are inline SVG from the one stroke set; never a text glyph.
5. A level may colour text and its left rule. It may not fill a surface.
6. Check a change at 320, 375 and 1440 px before opening a pull request. The local preview is
   `.claude/launch.json` (`python3 -m http.server 8766 --directory site`) after
   `scripts/build_site.py` has written `site/`, or after writing the pages alone with
   `build_site.write_pages` over a downloaded `site/data/spots.json`.
7. In `page.css`, the prose defaults for paragraph and list spacing are written as
   `:where(main.doc) p`, with no specificity, so that a component's class sets its own spacing.
   Written as `main.doc p`, a default outranks a single class such as `.note`, and the
   component's spacing is silently lost.

## Review refinements

The PR review kept the editorial identity but made the decision easier to scan: stronger sans-serif
risk headlines, readable day cells with aligned levels, plain place metadata instead of decorative
chips, a visible issue time, and 44 px controls. Secondary text and keyboard focus use shades that
stay legible on the page. The accuracy figures sit on the page between rules, rather than in four
more cards; their numbers use tabular sans-serif digits. Reliability bars use the link shade. On phones the prose header
gives all four navigation links a single full-width row.
