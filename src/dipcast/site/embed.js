// One spot's forecast as a card for another site's page: embed.html?spot=<id>, which a club or a
// council puts in an iframe (the About page says how, the terms allow it with the credits intact).
// The card holds the spot's headline and the five days by the page's own rules (levels.js, so it
// never disagrees with the spot's page or an alert), the caveat, when the forecast was issued, a
// link back to the spot's page and the data credits. Nothing is stored in the browser.
//
// A plain script, as levels.js: embed.html loads levels.js first, and this file finds its names
// there; Node requires both for the tests (tests/site_embed.test.cjs). Everything sits inside
// `Embed`, so no name here can clash with one of levels.js's.
const Embed = (() => {
  const R = typeof module === 'object' && module.exports ? require('./levels.js')
    : { ORDER, NO_OVERFLOWS, setToday, today, rank, risk, level, headline, weekNext, daily };   // levels.js's globals
  const PAGE_ID = /^[A-Za-z0-9_-]+$/;   // the page and build_site.py use the same rule
  const STALE_MIN = 8 * 60;   // the app's "Stale" notice waits as long (index.html, STALE_MIN)
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  const tone = l => l === R.NO_OVERFLOWS ? 'clear' : R.ORDER[l] === undefined ? 'na' : l.replace(' ', '');
  const shortDay = iso => iso === R.today() ? 'Today' : new Date(iso + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'short' });
  const ago = m => { const t = Math.max(0, Math.round(m)); return t < 60 ? `${t} min` : t % 60 ? `${Math.floor(t / 60)} h ${t % 60} min` : `${t / 60} h`; };
  // Links open the site in a new tab: inside a frame, a link that opened in place would leave the
  // reader with SwimSignal's page squeezed into a corner of someone else's.
  const out = (href, text, cls = '') => `<a${cls ? ` class="${cls}"` : ''} href="${esc(href)}" target="_blank" rel="noopener">${text}</a>`;
  const spotHref = id => PAGE_ID.test(id) ? `spot/${id}/` : `./?spot=${encodeURIComponent(id)}`;
  // The credits travel with the card, as with every data file (build_site.data_credits); the full
  // notices are on the terms page.
  const CREDIT = 'Data: water companies via the National Storm Overflow Hub (CC BY 4.0); Environment Agency and '
    + 'Ordnance Survey (OGL v3.0); Open-Meteo, from Met Office forecasts (CC BY 4.0, CC BY-SA 4.0). '
    + `None of these bodies endorses SwimSignal. ${out('terms.html#data', 'All credits and licences')}.`;

  // The five days as the spot's page shows them, a row each: the day, its level and a bar. The bar
  // fills the day's band, one quarter for low to four for very high. The spot's page also places a
  // day within its band (levelPlace in index.html); the card leaves that out rather than keep a
  // second copy of the rule.
  const rows = s => s.days.slice(0, 5).map(x => {
    const lv = R.risk(s, x).level;
    return `<li class="drow ${lv ? tone(lv) : 'na'}"><span class="d">${shortDay(x.date)}</span><span class="l">${lv ? cap(lv) : 'No data'}</span>`
      + `<span class="dbar" aria-hidden="true"><i style="width:${lv ? (R.rank(lv) + 1) * 25 : 0}%"></i></span></li>`;
  }).join('');

  // The caveat, as the spot's page puts it beside the forecast (check() in index.html). At a bathing
  // water the Environment Agency's page is the one place with advice against bathing given at short
  // notice, so it is linked here too.
  const check = s => {
    const url = (s.classification || {}).url;
    return 'A forecast, not a water test: check the signs at the water before you swim.'
      + (s.source === 'designated' && url ? ` ${out(url, 'The Environment Agency’s page')} has any advice against bathing there today, which this forecast does not include.` : '');
  };

  // The card's HTML for spot s from spots.json (data); now is a time in ms, for the age.
  function card(s, data, now = Date.now()) {
    R.setToday(String(data.generated_at).slice(0, 10));
    const page = spotHref(s.id), next = R.weekNext(s), age = (now - Date.parse(data.generated_at)) / 60000;
    const issued = new Date(data.generated_at).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    let h = `<h1 class="name">${out(page, esc(s.name))}</h1>`;
    if (age > STALE_MIN) h += `<p class="notice" role="alert"><b>Stale.</b> This forecast is ${ago(age)} old: the automatic update has not run since. Treat it as out of date.</p>`;
    h += `<p class="hl ${tone(R.level(s))}">${esc(R.headline(s))}</p>` + (next ? `<p class="next">${esc(next)}</p>` : '');
    if (R.daily(s) && s.days.length) h += `<h2 class="lab" id="days-h">Pollution risk, next five days</h2><ul class="drows" aria-labelledby="days-h">${rows(s)}</ul>`;
    return h + `<p class="check">${check(s)}</p>`
      + `<p class="foot">Issued ${esc(issued)}, ${ago(age)} ago · ${out(page, 'Full forecast on SwimSignal', 'back')}</p>`
      + `<p class="credit">${CREDIT}</p>`;
  }

  // No spot by that id (or none asked for): say how to find one, and keep the way to the site.
  function missing(id) {
    return `<h1 class="name">${id ? 'No spot with this id' : 'No spot chosen'}</h1>`
      + `<p class="say">${id ? `There is no spot “${esc(id)}”. ` : ''}The address needs a spot’s id after <code>?spot=</code>: `
      + 'the last part of the spot’s page address on SwimSignal, so <code>wharfe-burnsall</code> for swimsignal.co.uk/spot/wharfe-burnsall/.</p>'
      + `<p class="foot">${out('./', 'All spots on SwimSignal', 'back')}</p>`;
  }

  const failed = () => '<h1 class="name">The forecast could not be loaded</h1>'
    + `<p class="say">Try again later, or ${out('./', 'open SwimSignal', 'back')}.</p>`;

  // In the page: read the id from the address, fetch the forecast (no-cache: the server is asked each
  // time, as the app does) and fill the card.
  async function start(el) {
    const id = new URLSearchParams(location.search).get('spot');
    if (!id) { el.innerHTML = missing(null); return; }
    let data;
    try {
      const res = await fetch('data/spots.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (e) { el.innerHTML = failed(); return; }
    const s = (data.spots || []).find(x => x.id === id);
    if (!s) { el.innerHTML = missing(id); return; }
    document.title = `${s.name}: pollution risk forecast · SwimSignal`;
    el.innerHTML = card(s, data);
  }

  return { card, missing, failed, start, STALE_MIN };
})();

if (typeof module === 'object' && module.exports) module.exports = Embed;
else Embed.start(document.getElementById('card'));
