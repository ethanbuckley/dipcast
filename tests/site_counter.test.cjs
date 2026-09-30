// counter.js, the page-view counter's loader: it loads Cloudflare's script only for a browser that
// has not objected, and gives the script its token.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const src = fs.readFileSync(require('node:path').join(__dirname, '../src/dipcast/site/counter.js'), 'utf8')
  .replace('__TOKEN__', 'abcdefghij0123456789');

function run({stored = null, storage = true, gpc, dnt} = {}) {
  const added = [];
  const localStorage = {getItem: k => { if (!storage) throw new Error('blocked'); return k === 'dipcast.count' ? stored : null; }};
  const document = {createElement: () => { const attrs = {}; return {attrs, setAttribute: (k, v) => { attrs[k] = v; }}; },
                    head: {appendChild: el => added.push(el)}};
  vm.runInNewContext(src, {localStorage, document, navigator: {globalPrivacyControl: gpc, doNotTrack: dnt}});
  return added;
}

test('with no objection it loads the beacon once, with the token', () => {
  const added = run();
  assert.equal(added.length, 1);
  assert.equal(added[0].src, 'https://static.cloudflareinsights.com/beacon.min.js');
  assert.equal(added[0].defer, true);
  assert.deepEqual(JSON.parse(added[0].attrs['data-cf-beacon']), {token: 'abcdefghij0123456789'});
});
test('a browser that chose not to be counted loads nothing', () => {
  assert.equal(run({stored: 'off'}).length, 0);
});
test('Global Privacy Control and Do Not Track count as objecting', () => {
  assert.equal(run({gpc: true}).length, 0);
  assert.equal(run({dnt: '1'}).length, 0);
  assert.equal(run({dnt: '0'}).length, 1);
});
test('blocked storage does not break the page', () => {
  assert.equal(run({storage: false}).length, 1);   // no choice can have been kept, so none is honoured
});
