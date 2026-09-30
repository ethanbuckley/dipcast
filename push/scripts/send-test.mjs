// Explicit, one-recipient smoke test after the owner subscribes their own phone.
// VAPID keys must be the same as that subscription's application-server key.
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { isPushService } from '../src/index.js';
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
  await response.body?.cancel();
  return response.status;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3) throw new Error('Usage: node push/scripts/send-test.mjs /private/path/subscription.json');
    const data = JSON.parse(await readFile(process.argv[2], 'utf8'));
    const status = await sendTest(data.subscription ?? data, process.env);
    console.log(`Push service HTTP ${status}. Acceptance is not proof of delivery; check your phone.`);
    if (status < 200 || status >= 300) process.exitCode = 1;
  } catch {
    // Do not expose endpoint URLs, subscription contents or private keys in errors.
    console.error('Test not completed. Check the subscription file, matching VAPID environment, HTTPS SITE_URL and network.');
    process.exitCode = 1;
  }
}
