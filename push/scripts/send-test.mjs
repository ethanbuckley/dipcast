// Explicit, one-recipient smoke test after the owner subscribes their own phone.
// VAPID keys must be the same as that subscription's application-server key.
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { isPushService, redact } from '../src/index.js';
import { sendPush, vapidSigner } from '../src/webpush.js';

export async function sendTest(subscription, env, { fetch = globalThis.fetch, now = Date.now } = {}) {
  if (!isPushService(subscription?.endpoint)) throw new Error('A supported browser push subscription is required');
  const site = new URL(env.SITE_URL);
  if (site.protocol !== 'https:') throw new Error('SITE_URL must be HTTPS');
  const at = now();
  const response = await sendPush(subscription, {
    title: 'Dipspot test notification',
    body: 'Your notification test arrived. This is not a water-quality warning.',
    url: new URL('saved/', site.href.endsWith('/') ? site : new URL(site.href + '/')).href,
    tag: 'dipspot-device-test',
    issued_at: new Date(at).toISOString(),
    expires_at: new Date(at + 60000).toISOString(),
  }, vapidSigner(env), { fetch, ttl: 60 });
  // A refusal's body says why (a VAPID key that does not match the subscription, say).
  const reason = response.ok ? '' : redact(await response.text().catch(() => ''), subscription.endpoint);
  if (response.ok) await response.body?.cancel();
  return { status: response.status, reason };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let subscription;
  try {
    if (process.argv.length !== 3) throw new Error('usage: node push/scripts/send-test.mjs /private/path/subscription.json');
    let data;
    // Not JSON.parse's own message: it quotes the start of the file, which is the endpoint.
    try { data = JSON.parse(await readFile(process.argv[2], 'utf8')); } catch (err) { throw new Error(err.code ? `cannot read ${process.argv[2]}` : 'the subscription file is not JSON'); }
    subscription = data.subscription ?? data;
    const { status, reason } = await sendTest(subscription, process.env);
    if (status >= 200 && status < 300) console.log(`Push service HTTP ${status}. Acceptance is not proof of delivery; check your phone.`);
    else { console.error(`Push service HTTP ${status}${reason ? `: ${reason}` : ''}`); process.exitCode = 1; }
  } catch (err) {
    // The message, with the endpoint cut out: it names what to fix (a missing VAPID variable, say).
    console.error(`Test not completed: ${redact(err.message, subscription?.endpoint)}`);
    process.exitCode = 1;
  }
}
