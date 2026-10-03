// The Worker on this computer, to try reviews end to end with a local build of the site before
// anything is deployed: SQLite in memory stands in for D1 and a Map for KV (the tests' own stand-ins),
// so everything is gone when it stops. Not for production. README.md, "Try it on this computer".
//
//   node scripts/dev-server.mjs            (from reviews/; PORT, ALLOWED_ORIGIN, SITE_URL, ADMIN_TOKEN to change)
import { createServer } from 'node:http';
import worker from '../src/index.js';
import { FakeD1, FakeKV } from '../test/helpers.js';

const port = Number(process.env.PORT || 8787);
const site = process.env.SITE_URL || 'http://localhost:8766/';
const env = {
  DB: new FakeD1(), PHOTOS: new FakeKV(),
  SITE_URL: site, ALLOWED_ORIGIN: process.env.ALLOWED_ORIGIN || new URL(site).origin,
  ADMIN_TOKEN: process.env.ADMIN_TOKEN || 'local-admin-token',
};

createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) headers.set(k, Array.isArray(v) ? v.join(', ') : v);
  headers.set('CF-Connecting-IP', req.socket.remoteAddress || 'unknown');   // as Cloudflare adds it
  const request = new Request(`http://localhost:${port}${req.url}`, {
    method: req.method, headers, body: ['GET', 'HEAD'].includes(req.method) ? undefined : Buffer.concat(chunks),
  });
  let response;
  try { response = await worker.fetch(request, env); } catch (err) { console.error(err); response = new Response(`Worker error: ${err.message}`, { status: 500 }); }
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
  console.log(req.method, req.url, response.status);
}).listen(port, () => {
  console.log(`Reviews Worker on http://localhost:${port}/ for the site at ${site}`);
  console.log(`Moderation: http://localhost:${port}/moderate (token: ${env.ADMIN_TOKEN})`);
});
