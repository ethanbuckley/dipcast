// The page-view counter's loader (Cloudflare Web Analytics). build_site.py puts this file inline in
// every page, with the token in place of the placeholder below, only when the counter is on.
// Since 5 Feb 2026 PECR (Schedule A1) lets a counter run without consent only with clear information
// (the privacy notice) and a free, simple way to object. The way to object is the button under
// "Saved spots, offline use and your data", which stores COUNT_KEY; a browser that sends Global
// Privacy Control or Do Not Track has objected already. Cloudflare's script reads its token from
// document.currentScript, which a script added this way still has.
(function () {
  var COUNT_KEY = 'dipcast.count';
  try { if (localStorage.getItem(COUNT_KEY) === 'off') return; } catch (e) { /* no storage: no choice kept to honour */ }
  if (navigator.globalPrivacyControl === true || navigator.doNotTrack === '1') return;
  var s = document.createElement('script');
  s.defer = true;
  s.src = 'https://static.cloudflareinsights.com/beacon.min.js';
  s.setAttribute('data-cf-beacon', '{"token": "__TOKEN__"}');
  document.head.appendChild(s);
})();
