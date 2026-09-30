import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sendTest } from '../scripts/send-test.mjs';
import { makeUserAgent, makeVapidEnv } from './helpers.js';

test('the device smoke test sends one clearly labelled, short-lived notification', async () => {
  const ua = makeUserAgent('https://web.push.apple.com/test-device');
  const env = { ...await makeVapidEnv(), SITE_URL: 'https://example.org/dipcast/' };
  const at = Date.parse('2026-09-30T08:00:00Z');
  let calls = 0;
  const { status } = await sendTest(ua.subscription, env, { now: () => at, fetch: async (url, init) => {
    calls++;
    assert.equal(url, ua.subscription.endpoint);
    assert.equal(init.headers.TTL, '60');
    const payload = ua.read(init.body);
    assert.equal(payload.title, 'SwimSignal test notification');
    assert.match(payload.body, /not a water-quality warning/);
    assert.equal(payload.url, env.SITE_URL + 'saved/');
    assert.equal(Date.parse(payload.expires_at), at + 60000);
    assert.ok(init.signal instanceof AbortSignal);
    return new Response(null, { status: 201 });
  } });
  assert.equal(status, 201);
  assert.equal(calls, 1);
});

test('device testing rejects unsupported destinations before contacting them', async () => {
  let calls = 0;
  await assert.rejects(sendTest({ endpoint: 'https://unrelated.example/' }, {}, { fetch: async () => { calls++; } }), /supported/);
  assert.equal(calls, 0);
});

test('a refused device test reports the push service\'s reason, without the endpoint', async () => {
  const ua = makeUserAgent('https://web.push.apple.com/test-device-token');
  const env = { ...await makeVapidEnv(), SITE_URL: 'https://example.org/dipcast/' };
  const refusal = JSON.stringify({ reason: 'BadJwtToken', endpoint: ua.subscription.endpoint });
  const out = await sendTest(ua.subscription, env, { fetch: async () => new Response(refusal, { status: 403 }) });
  assert.equal(out.status, 403);
  assert.match(out.reason, /BadJwtToken/);
  assert.ok(!out.reason.includes('test-device-token'));
});
