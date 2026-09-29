// Each spot's level and headline, for the alerts service (push/): written beside spots.json by
// scripts/build_site.py. The rules are the page's own (src/dipcast/site/levels.js), run here in
// Node, so an alert says what the page says.
//
//   node scripts/alerts.js site/data/spots.json site/data/alerts.json https://example.org/swim/
const fs = require('fs');
const path = require('path');
const L = require(path.join(__dirname, '..', 'src', 'dipcast', 'site', 'levels.js'));

const [src, out, root] = process.argv.slice(2);
if (!src || !out || !root || !root.endsWith('/')) {
  console.error('usage: node scripts/alerts.js <spots.json> <alerts.json> <site root ending in />');
  process.exit(2);
}
const data = JSON.parse(fs.readFileSync(src, 'utf8'));
L.setToday(data.generated_at.slice(0, 10));
const PAGE_ID = /^[A-Za-z0-9_-]+$/;   // the page and build_site.py use the same rule
const spots = {};
for (const s of data.spots) {
  const level = L.level(s);
  spots[s.id] = { name: s.name, rank: L.rank(level), level, headline: L.headline(s),
    url: PAGE_ID.test(s.id) ? `${root}spot/${s.id}/` : `${root}?spot=${encodeURIComponent(s.id)}` };
}
fs.writeFileSync(out, JSON.stringify({ generated_at: data.generated_at, spots }));
