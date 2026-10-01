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

- **Source Serif 4** for headings, spot names, the headline level and the big numbers. A serif at
  600 weight, never heavier. Variable weight and optical size, so it is sturdy at 18 px and fine at
  34 px.
- **Source Sans 3** for everything else: body, labels, controls, tables. Weights 400 and 600, and
  700 for a level word beside its label. Its digits are one width, so numbers align in tables without a feature flag.
- The monospaced stack is kept for one thing: model version strings.

Sizes, in pixels. App: body 16, meta 13, labels 13 to 14, card heading 18, spot name 28, headline
level 28, page heading 28. Prose pages: body 17, lede 19, h1 34, h2 23, h3 19. Line height 1.5 for
text, 1.15 to 1.25 for headings. No letter-spacing beyond −0.01em on the largest headings, and no
uppercase labels anywhere.

### Colour

- Page `#f4f5f3`, card white, ink `#1b2328`, muted `#58636b`, hairlines `#dfe3e1`. Dark mode
  redefines every token; nothing is hard-coded in a rule.
- Brand teal `#0f5a61` (the icon's) for the bar, links and the primary button; `#5cc2b5` for focus
  rings and links on dark backgrounds.
- The four levels keep their meaning and are the only strong colours on a page. Marks (map, day
  strips, bars): low `#2f8f58`, moderate `#d49a06`, high `#d6621a`, very high `#bf2a2a`. Text
  (headlines, values): `#1e6a41`, `#6f5300`, `#93400f`, `#961d1d`, which pass 4.5:1 on white. A
  spot with no monitored overflow upstream is teal `#4aa39a`, not grey: it is a calm answer, not a
  missing one. Grey `#98a2aa` means no level.
- A level colours the headline and a 4 px rule on its card. It never fills a card: the washed-tint
  card was the dashboard look, and five of them in a column were a wall of pastel.

### Shape and surface

- One corner radius, 8 px, for cards, inputs, buttons, chips and the day strip. Count badges and
  dots are round, because they are circles. Nothing else is a pill.
- Cards are white with a 1 px hairline border. No shadows and no gradients, except a 1 px lift
  under the controls that sit on the map, which need to read against tiles.
- The five days are one strip, a bordered box of five cells divided by hairlines, each cell with
  its level as a 4 px bar at the top. The open day is shown by an inset ring, not a glow.
- Saved cards and the hero carry their level as a 4 px left rule.

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
- **Accuracy page:** the four numbers that matter as tiles at the top, a short "in short" list,
  a contents list, then sections in the same order as before. Tables share one style; the three
  reliability tables draw forecast against observed as bars. The scoring rules fold away under
  "How the live scoring works", so the page opens on results.

### What was kept on purpose

The information architecture (list → spot → day), every word of the terms and privacy notice, the
accessibility work (focus rings, `aria-pressed`, 16 px inputs so iOS does not zoom, 44 px targets),
the phone's bottom bar, offline behaviour, and the level rules in `levels.js`. The redesign changed
how things look, not what the site says.

## Rules for changes

1. Add a colour, size or radius only as a token in `page.css`, and mirror it in `index.html`.
2. Headings in the serif, 600; everything else in the sans. No uppercase labels, no tracking.
3. No new radius, shadow or gradient. If a surface needs separating, use a hairline.
4. Icons are inline SVG from the one stroke set; never a text glyph.
5. A level may colour text and a 4 px rule. It may not fill a surface.
6. Check a change at 375 px and at 1440 px, in light and dark, before opening a pull request. The
   local preview is `.claude/launch.json` (`python3 -m http.server 8766 --directory site`) after
   `scripts/build_site.py` has written `site/`, or after writing the pages alone with
   `build_site.write_pages` over a downloaded `site/data/spots.json`.
