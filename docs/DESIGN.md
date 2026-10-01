# SwimSignal design system

Written 1 October 2026, when the site was redrawn to look like one product rather than a set of
generated pages. This file is the reference for anyone changing how the site looks: what was wrong,
what was decided, and the rules that keep the pages consistent. The stylesheet that carries the
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

Sizes, in pixels. App: body 16, meta 13, labels 13 to 14, card heading 18, spot name 28, headline
level 32 (28 on the smallest phones), page heading 28. Prose pages: body 17, lede 19, h1 34, h2 23,
h3 19, with h1 28 and lede 17 on the smallest phones (up to 360 px); boxed text (the summary,
notices) 16; notes, hints and table rows 15, table headers 14. Line height 1.5 for text, 1.15 to
1.25 for headings. No letter-spacing beyond −0.01em on the largest headings, and no uppercase
labels anywhere.

### Colour

- Page `#f4f5f3`, card white, ink `#1b2328`, muted `#58636b`, hairlines `#dfe3e1`. Dark mode
  redefines every token; nothing is hard-coded in a rule.
- Brand teal `#0f5a61` (the icon's) for the bar, links and the primary button; `#0f5a61` for focus
  rings on light surfaces and `#7fd3c7` on dark ones. The low-risk filter uses `#1e6a41` with
  white text, rather than the brighter map green.
- The four levels keep their meaning and are the only strong colours on a page. Marks (map, day
  strips, bars): low `#2f8f58`, moderate `#d49a06`, high `#d6621a`, very high `#bf2a2a`. Text
  (headlines, values): `#1e6a41`, `#6f5300`, `#93400f`, `#961d1d`, which pass 4.5:1 on white. A
  spot with no monitored overflow upstream is teal `#4aa39a`, not grey: it is a calm answer, not a
  missing one. Grey `#98a2aa` means no level.
- A level colours the headline and a 6 px rule on the hero (4 px on Saved cards). The written
  level in bold sans-serif carries the meaning even when colour is hard to see; the rule is a
  supporting cue. It never fills a card: the washed-tint
  card was the dashboard look, and five of them in a column were a wall of pastel.

### Shape and surface

- One corner radius, 8 px, for cards, inputs, buttons, chips and the day strip. Count badges and
  dots are round, because they are circles. Nothing else is a pill.
- Cards are white with a 1 px hairline border. No shadows and no gradients, except a 1 px lift
  under the controls that sit on the map, which need to read against tiles.
- The five days are one strip, a bordered box of five cells divided by hairlines, each cell with
  its level as a 4 px bar at the top. The open day is shown by an inset ring, not a glow.
- Saved cards carry a 4 px left rule; the hero uses 6 px. Its forecast issue time sits beside
  the answer, before the five days, so freshness is visible at the point of deciding.

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

### Layout

- **Header**, shared by every page: the brand bar with the mark, the serif wordmark and the main
  links (Explore, Accuracy, About, Feedback; the app adds Saved). The current page is underlined.
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
- `theme-color`, the bar's colour, so the browser's own bar matches it: `#0f5a61`, and `#0b474d`
  in dark mode;
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
adds the manifest and the Home Screen tags.

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

### What was kept on purpose

The information architecture (list → spot → day), every word of the terms and privacy notice, the
accessibility work (focus rings, `aria-pressed`, 16 px inputs so iOS does not zoom, 44 px targets),
the phone's bottom bar, offline behaviour, and the level rules in `levels.js`. The redesign changed
how things look, not what the site says.

## Rules for changes

1. Add a colour, size or radius only as a token in `page.css`, and mirror it in `index.html`.
2. Editorial headings in the serif, 600; operational headings, risk levels and numbers in the sans. No uppercase labels, no tracking.
3. No new radius, shadow or gradient. If a surface needs separating, use a hairline.
4. Icons are inline SVG from the one stroke set; never a text glyph.
5. A level may colour text and its left rule. It may not fill a surface.
6. Check a change at 375 px and at 1440 px, in light and dark, before opening a pull request. The
   local preview is `.claude/launch.json` (`python3 -m http.server 8766 --directory site`) after
   `scripts/build_site.py` has written `site/`, or after writing the pages alone with
   `build_site.write_pages` over a downloaded `site/data/spots.json`.
7. In `page.css`, the prose defaults for paragraph and list spacing are written as
   `:where(main.doc) p`, with no specificity, so that a component's class sets its own spacing.
   Written as `main.doc p`, a default outranks a single class such as `.note`, and the
   component's spacing is silently lost.

## Review refinements

The PR review kept the editorial identity but made the decision easier to scan: stronger sans-serif
risk headlines, readable day cells with aligned levels, plain place metadata instead of decorative
chips, a visible issue time, and 44 px controls. Secondary text and keyboard focus now use shades
that remain legible in both themes. The accuracy figures sit on the page between rules, rather
than in four more cards; their numbers use tabular sans-serif digits. Reliability bars use the
theme's link shade so forecast bars remain visible in dark mode. On phones the prose header
gives all four navigation links a single full-width row.
