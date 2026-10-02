# Design handoff: making SwimSignal look designed, not generated

Written 2 October 2026 for the next session, by the session that ran design rounds one to four
(PRs #37, #38, #39, #40 merged; #41 open). Ethan's verdict after those rounds: *"I still think the
site looks AI generated"*, and *"I'm not sure I like the dark mode"*. This file says what was
done, what still reads as generated and why, the direction to take, and how to work in this
repository without breaking what the build and the tests rely on. Read `docs/DESIGN.md` next: it is
the current system and its rules, and this file changes some of them.

## 1. Where things stand

- Live site: https://swimsignal.co.uk (GitHub Pages, custom domain). Every push to `main` that
  touches `src/**` rebuilds and deploys it within about ten minutes.
- Rounds one to four, in order: typefaces served from the site (Source Serif 4 for headings,
  Source Sans 3 for everything else), one shared stylesheet for the prose pages, one 8 px radius,
  hairline borders, no shadows, inline SVG icons, the level as a coloured rule; then the list made
  answer-first, the hero tightened, Leaflet's controls restyled; then "Lower risk nearby", folded
  explanations, a calmer map; then Back keeping its place, Nearby on every spot, one-step feedback
  and the water-quality wording ("Very high risk", never a bare "Very high" beside "Water quality").
- GPT 6.1 reviewed round one and moved risk headlines, card headings and figures from the serif to
  bold sans, keeping the serif for the wordmark, page headings and spot names.
- Tests: `uv run pytest -q` (56) and `node --test tests/site_*.test.cjs` (22). Both must pass.

## 2. Why it still reads as generated

Looked at on 1 October 2026 at 375 px and 1440 px. Be specific when you fix these; vague
"polish" is how the last rounds spent their effort on the wrong things.

1. **Boxes in a column.** A spot page is seven to nine white cards with 1 px borders, stacked:
   hero, Nearby, Right now, River level, Upstream map, Overflows, Day-by-day, actions. The Saved
   page puts a bordered day strip inside a bordered card. The list sits in a bordered container.
   Equal boxes with equal borders is the admin-dashboard pattern, and it is the single biggest
   tell. A designed page has one emphasised object and lets whitespace and headings do the rest.
2. **The header bar.** A full-bleed, saturated teal bar with white text on every page is the
   loudest element on screen and it is the same on every page: it reads as a template's navbar,
   not as a brand. It also fights the map and the hero for attention.
3. **Flat, near-identical surfaces.** Page `#f4f5f3`, card `#fff`: so little difference that the
   design has to draw borders around everything to separate anything. That is why there are so
   many boxes.
4. **Too many type sizes.** The CSS uses 12, 12.5, 13, 13.5, 14, 14.5, 15, 15.5, 16, 17, 18, 19,
   20, 21, 26, 28, 32 and more. A system has about six. The small sizes carry most of the UI.
5. **Small grey text under everything.** A spot page has at least fifteen `.small.muted`
   blocks: strip note, issued line, a note under every card, a caveat under the hero, a note under
   the list. Honesty needs a few of these; the rest are habit.
6. **The map looks like a Leaflet demo.** Desaturated OpenStreetMap tiles with circle markers. The
   level colours are the only thing a swimmer needs from it, and the tiles compete with them.
7. **No image, texture or motif.** A product about rivers, lakes and cold dawns has nothing but
   grey rectangles and one icon. The brand mark (a river, an overflow, a swim spot) is good and is
   used nowhere but the corner.
8. **Every control is the same rectangle.** Inputs, buttons, chips, the sort control, the day
   strip, cards: one radius, one border, one weight. Uniformity at that level reads as framework
   defaults.
9. **Long sentences in the interface.** The strip note is three lines; each factor row has a
   two-line description; the alerts card carries a consent paragraph; "About these forecasts" with
   four folds sits under every view, including every spot page.
10. **Dark mode** is a blue-grey IDE theme with pastel level colours. It is not designed; it is
    the light theme with inverted tokens.

## 3. Direction: a field guide, not a dashboard

Opinionated on purpose. Check each decision in screenshots at 375 px and 1440 px before and
after, and keep what survives the comparison.

### Surfaces and layout
- **One box per page.** The hero (the answer) is the only card: white, the level as its 6 px left
  rule. Everything else is unboxed: a serif section heading, a hairline rule above it, content,
  whitespace. Overflows and Day-by-day become ruled tables and lists, not cards. The Saved page's
  spots become ruled entries with the strip, not cards in cards. The list loses its container
  border and keeps the row dividers.
- **A warmer page.** Move the page from cool grey to a warm paper (`#f6f4ee` or near it, test it
  against the level colours) so that the white hero reads as the one lifted object without any
  border, and the map's grey tiles sit as a different material.
- **A light header.** Paper background, the mark and the serif wordmark in teal, the links in
  ink, a hairline below. One brand accent at the very top of the page is enough. The phone's
  bottom bar stays as it is: it works.
- **Rhythm.** Larger steps between sections (32 and 40 px), smaller within (8 and 12). The spot
  name larger (34 to 36 px on desktop, 30 on phones), the headline level 30 to 32 in bold sans,
  and then a real drop to body text. Fewer, bigger moments.

### Type
- Keep the pair. Cut the scale to six sizes: 13 (meta, used sparingly), 15 (notes), 17 (body),
  20 (section headings), 30 (headline level), 36 (spot name and page titles; 30 on phones).
  Remove every other size.
- Halve the small grey text: see §4 for what goes and what stays.

### Colour
- Keep the teal (`#0f5a61`) as the one accent. Recalibrate the four level colours to sit in the
  same natural palette rather than the alert-colour defaults: moss for low, ochre for moderate,
  rust for high, brick for very high (candidates: `#3b7d4f`, `#b7791f`, `#c2552a`, `#a32d2d`;
  check AA for text shades on paper and white, and keep the map markers identical). Define them
  once in `page.css` and mirror them in `index.html`; `colour()` in the script has the marker hex.
- Decide dark mode (§5) before touching colour, so you tune one palette, not two.

### The map
- Make the tiles monochrome (`filter: grayscale(1) brightness(1.04) contrast(.95)` or similar) so
  that the markers are the only colour on it, and lower the legend to the four level dots plus
  "No overflows upstream" and "No level" in one line. Keep OpenStreetMap: the privacy notice
  lists it, and a new tile provider would need a notice change.
- Markers: a slightly larger dot with a 2 px paper ring; the open spot ringed in teal as now.

### A signature
- Use the brand mark's river line as the one decorative element: a thin teal curve under the list's
  heading and on empty states, drawn once as inline SVG. Nothing else decorative. If Ethan wants
  photographs of spots later, that is a separate project (sourcing and licences).

### Controls
- Keep the 8 px radius for inputs and buttons; drop the border from the chips and the sort control
  in favour of text weight and a filled state, so not everything is a bordered rectangle. The day
  strip keeps its cells but loses its outer border on paper, keeping only the level bars and the
  hairlines between cells.

## 4. Words to cut, fold or keep

Cut or fold (meaning kept, lines removed):
- The strip note under the five days: fold it under the existing "What this means" (now "What <label> means"); leave the †
  sentence visible only when a † is on screen.
- Factor rows: one sentence visible, the rest in the row's fold (round four did this for the
  water row; do it for the spills row's second sentence and the EA rating's history clause).
- "About these forecasts": on the list view only. A spot page and the Saved page get one line,
  "About these forecasts · How accurate is it?", linking to it.
- The list's bottom note: one line ("Updated several times a day. What the levels mean.").
- The Saved page lede: one sentence.
- The alerts card: keep the consent text (it is what makes consent informed) but fold it under
  "What turning alerts on sends", with the first sentence visible.

Keep, visible, once per view: "A forecast, not a water test: check the signs at the water before
you swim"; every Environment Agency advice sentence; the issue time; the data credits in the foot;
every sentence of the terms and the privacy notice (do not edit those files' wording at all).

## 5. Dark mode: decide, then act

Ethan is not sure he likes it, and the present one is not designed. Recommendation: **remove it
and ship light only**, for three reasons. The product is read outdoors, where the light theme's
contrast is what matters; a dark theme doubles the surface to design and check, and the last four
rounds spent effort keeping two palettes in step; and a good dark theme is a design in its own
right, not inverted tokens. If Ethan later wants one, add it as a deliberate toggle with its own
palette.

How to remove it: delete the `@media (prefers-color-scheme: dark)` blocks from `page.css` and
`index.html`; set `color-scheme: light` in both; make `--tile-filter` a single value; replace the
two `theme-color` metas with one (`#0f5a61`, or the new header colour) in `index.html`, the six
prose pages and `not_found_page()` in `scripts/build_site.py`; remove the `DARK` media query and
`RING()`'s dark branch in the script (keep `RING` returning the teal); update `docs/DESIGN.md`.
Check the share picture still draws (it reads tokens with `getComputedStyle`).

If Ethan decides to keep it instead, design it: a true dark paper (`#141a1c` with a warm cast),
the level colours re-tuned for dark (not pastel versions), the header the same material as the
page, and the map tiles dimmed rather than desaturated to mud.

## 6. How to work here

- **Preview without the data pipeline.** `mkdir -p site/data` and download
  `https://swimsignal.co.uk/data/spots.json`, `verification.json` and `overflows.geojson` into
  it, then write the pages with `build_site.write_pages(Path('site'), spots, root='http://localhost:8766/', day='2026-10-02', push=True)`
  (see `scripts/build_site.py`), and serve with the `site` entry in `.claude/launch.json`
  (`python3 -m http.server 8766 --directory site`). The home page will show a "Stale" notice once
  the downloaded forecast is over eight hours old; that is expected locally.
- **Check every change at 320, 375 and 1440 px**, on: the list with and without saved spots
  (`localStorage['dipcast.saved']` is a JSON array of ids), a river spot with overflows
  (`spot/eden-armathwaite/`), a lake with none (`spot/buttermere/`), a poor-rated bathing water
  (`spot/bw-uke4200-08904/`), a picked day (`#day=…`), the Saved page, the phone's full map, and
  the prose pages. Ethan judges by screenshots; take them before and after.
- **Strings the build and tests pin** (do not change): in `index.html` the placeholder
  `<div id="result"><p class="muted">Loading forecasts…</p></div>`, the `<!-- page-meta … -->`
  block, `<a href="about.html">About SwimSignal</a>`, `<a href="about.html" class="desk-only">About</a>`,
  `None of these bodies endorses SwimSignal`, `href="terms.html#data"`, the EA river-level
  attribution line, `<script src="experience.js">`, `const PAGE_ID = /^[A-Za-z0-9_-]+$/;`, the
  `/\/spot\/([A-Za-z0-9_-]+)\/?$/` regex, `replace(/(spot\/[^/]+|saved)\/?$/, '')`,
  `id="count-btn"`, `getElementById('page-counter')`; never the words "advises against bathing".
  In the prose pages: nav links as `<a href="/about">About</a>` with any `aria-current` before
  `href`, no `href="/…"` that `REWRITES` in `build_site.py` does not map, the two `<span id="n-…">`
  counts in `about.html`, `function prepareEmail(` and `$('feedback-form').addEventListener` and
  "Send by email" in `feedback.html`, and the `PUSH_SWAPS` sentences in `privacy.html`.
- **Tokens live twice**: `page.css` for the prose pages and inline in `index.html` for the app,
  so the app paints before any stylesheet and works offline. Change both.
- **Fonts** are in `src/dipcast/api/static/fonts/` (Latin subsets, SIL OFL, licence beside them).
  `og.png` is drawn by `scripts/make_share_image.py` (needs TTFs; see its docstring).
- **Pull requests**: against `main`, never stacked; Ethan merges within the hour, so check a PR's
  state before pushing to its branch. Each round so far was one PR per file scope; one PR for a
  coherent visual change is fine now that the three original branches have merged. End commit
  messages and PR bodies with the attribution lines the session is given.
- **Working with subagents**: it worked well to give one agent one file (`index.html`) in an
  isolated worktree, with the brief in a file, and to integrate, check in the browser and ship from
  the orchestrating session. Two agents must never share a file. The shared brief from rounds two
  to four is reproduced in §8 of this file in short form.
- **Memory**: the orchestrating sessions' notes are in Ethan's Claude memory under
  `project-design-system-prs` and `feedback-design-taste`.

## 7. What not to redo

The information architecture (list → spot → day), the honesty copy and the level rules in
`levels.js` are settled. The answer-first list, the Nearby card, Back keeping its place, the
one-step feedback form and the "Very high risk" wording were asked for or approved by Ethan. The
serif-for-names, sans-for-levels split came from the GPT review and Ethan agreed with it. Do not
reopen those; spend the effort on §3 and §4.

## 8. The brief in short, for any agent you spawn

The user is a wild swimmer on a phone, outdoors, asking: is my spot all right today or this
weekend; where near me is low on Saturday; why is it high and when does it ease. Answer first,
explanation one tap away, the caveat once per view. One emphasised object per page; whitespace and
ruled headings, not boxes. Six type sizes. One accent plus four natural level colours. No shadows,
gradients, uppercase labels or text glyphs. Inline SVG icons from one stroke set. Every colour a
token, defined in `page.css` and mirrored in `index.html`. All targets 44 px, inputs 16 px or
more. No new third-party requests. Keep every pinned string and every legal sentence. Verify with
pytest, the Node tests, `node --check` on the page script, and screenshots at 320, 375 and 1440 px.

## 9. Open items outside design

A real-phone check in sunlight (never done). The iPhone Home Screen alert test (`push/`). The API
server is not deployed, so API-only changes reach nobody. Ethan's winter tester group starts on
19 October 2026; the site should be visually settled before then.
