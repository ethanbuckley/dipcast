import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createECDH, createPublicKey, verify } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { b64url, ecJwk, encrypt, importEcdhPublic, unb64url, vapidSigner } from '../src/webpush.js';
import { decrypt, makeUserAgent, makeVapidEnv } from './helpers.js';

// RFC 8291 Appendix A, all values base64url.
const RFC = {
  plaintext: 'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24',
  asPrivate: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',
  asPublic: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
  uaPrivate: 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94',
  uaPublic: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
  salt: 'DGv6ra1nlYgDCS1FRnbzlw',
  auth: 'BTBZMqHH6r4Tts7J_aSIgg',
  body: 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN',
};

const uaFromRfc = () => {
  const ua = createECDH('prime256v1');
  ua.setPrivateKey(Buffer.from(RFC.uaPrivate, 'base64url'));
  return ua;
};

test('RFC 8291 Appendix A: encryption produces the RFC body exactly', async () => {
  assert.equal(Buffer.from(RFC.plaintext, 'base64url').toString(), 'When I grow up, I want to be a watermelon');
  const asPublic = unb64url(RFC.asPublic);
  const senderKeys = {
    publicKey: await importEcdhPublic(asPublic),
    privateKey: await crypto.subtle.importKey('jwk', ecJwk(asPublic, RFC.asPrivate), { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']),
  };
  const body = await encrypt(unb64url(RFC.plaintext), { p256dh: RFC.uaPublic, auth: RFC.auth }, { salt: unb64url(RFC.salt), senderKeys });
  assert.equal(b64url(body), RFC.body);
});

test('RFC 8291 Appendix A: the vector is self-consistent under the independent decryptor', () => {
  const ua = uaFromRfc();
  assert.equal(ua.getPublicKey('base64url'), RFC.uaPublic);
  const plain = decrypt(Buffer.from(RFC.body, 'base64url'), ua, Buffer.from(RFC.auth, 'base64url'));
  assert.equal(plain.toString('base64url'), RFC.plaintext);
});

test('round trip with fresh keys and salt; header layout is salt | rs=4096 | 65 | sender key', async () => {
  const ua = makeUserAgent('https://push.example.net/x');
  const message = { title: 'Pangbourne', body: 'High tomorrow' };
  const a = await encrypt(new TextEncoder().encode(JSON.stringify(message)), ua.subscription.keys);
  const b = await encrypt(new TextEncoder().encode(JSON.stringify(message)), ua.subscription.keys);
  assert.deepEqual(ua.read(a), message);
  assert.notDeepEqual(a.subarray(0, 16), b.subarray(0, 16), 'salt must differ per message');
  assert.equal(new DataView(a.buffer).getUint32(16), 4096);
  assert.equal(a[20], 65);
  assert.equal(a[21], 4);
  assert.notDeepEqual(a.subarray(21, 86), b.subarray(21, 86), 'sender key must differ per message');
});

test('refuses a plaintext that would make the body exceed 4096 bytes', async () => {
  const { keys } = makeUserAgent('https://push.example.net/x').subscription;
  assert.equal((await encrypt(new Uint8Array(3993), keys)).length, 4096);
  await assert.rejects(encrypt(new Uint8Array(3994), keys), /too large/);
});

test('base64url helpers round-trip and tolerate padding', () => {
  for (let n = 0; n < 40; n++) {
    const bytes = crypto.getRandomValues(new Uint8Array(n));
    assert.deepEqual(unb64url(b64url(bytes)), bytes);
    assert.equal(b64url(bytes), Buffer.from(bytes).toString('base64url'));
  }
  assert.deepEqual(unb64url('AQ=='), Uint8Array.of(1));
  assert.throws(() => unb64url('a+b/'));
});

test('VAPID: header format, claims, and a signature that verifies with the public key', async () => {
  const env = await makeVapidEnv('mailto:owner@example.com');
  const now = Date.UTC(2026, 8, 29, 12);
  const authorize = vapidSigner(env, () => now);
  const header = await authorize('https://fcm.googleapis.com/fcm/send/abc:def?x=1');

  const m = /^vapid t=([\w-]+)\.([\w-]+)\.([\w-]+), k=([\w-]+)$/.exec(header);
  assert.ok(m, header);
  const [, h, c, s, k] = m;
  assert.equal(k, env.VAPID_PUBLIC_KEY);
  assert.deepEqual(JSON.parse(Buffer.from(h, 'base64url')), { typ: 'JWT', alg: 'ES256' });
  const claims = JSON.parse(Buffer.from(c, 'base64url'));
  assert.equal(claims.aud, 'https://fcm.googleapis.com');
  assert.equal(claims.sub, 'mailto:owner@example.com');
  assert.equal(claims.exp, now / 1000 + 12 * 3600);
  assert.ok(claims.exp - now / 1000 < 24 * 3600);

  const signed = new TextEncoder().encode(`${h}.${c}`);
  const sig = Buffer.from(s, 'base64url');
  assert.equal(sig.length, 64);
  const pub = unb64url(env.VAPID_PUBLIC_KEY);
  const webKey = await crypto.subtle.importKey('raw', pub, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  assert.ok(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, webKey, sig, signed));
  // node:crypto as a second, independent verifier of the JWS (r||s) signature form.
  const nodeKey = createPublicKey({ key: ecJwk(pub), format: 'jwk' });
  assert.ok(verify('sha256', signed, { key: nodeKey, dsaEncoding: 'ieee-p1363' }, sig));

  assert.equal(await authorize('https://fcm.googleapis.com/fcm/send/other'), header, 'one JWT per push service');
  assert.notEqual(await authorize('https://updates.push.services.mozilla.com/wpush/v2/x'), header);
});

test('VAPID: refuses a subject that is not mailto: or https:, and a missing private key', async () => {
  const env = await makeVapidEnv();
  assert.throws(() => vapidSigner({ ...env, VAPID_SUBJECT: 'REPLACE_WITH_mailto:you@example.com' }), /VAPID_SUBJECT/);
  assert.throws(() => vapidSigner({ ...env, VAPID_PRIVATE_KEY: undefined }), /VAPID_PRIVATE_KEY/);
  assert.throws(() => vapidSigner({ ...env, VAPID_PUBLIC_KEY: 'REPLACE_WITH_PUBLIC_KEY' }), /65-byte/);
});

test('scripts/vapid-keys.mjs prints a working key pair in two lines', async () => {
  const script = fileURLToPath(new URL('../scripts/vapid-keys.mjs', import.meta.url));
  const { stdout } = await promisify(execFile)(process.execPath, [script]);
  const lines = stdout.trim().split('\n');
  assert.equal(lines.length, 2);
  const [, pub] = /^VAPID_PUBLIC_KEY=([\w-]+)$/.exec(lines[0]);
  const [, priv] = /^VAPID_PRIVATE_KEY=([\w-]+)$/.exec(lines[1]);
  assert.equal(unb64url(pub).length, 65);
  assert.equal(unb64url(priv).length, 32);

  const header = await vapidSigner({ VAPID_PUBLIC_KEY: pub, VAPID_PRIVATE_KEY: priv, VAPID_SUBJECT: 'https://example.com' })('https://push.example.net/1');
  const [, h, c, s] = /t=([\w-]+)\.([\w-]+)\.([\w-]+)/.exec(header);
  const key = await crypto.subtle.importKey('raw', unb64url(pub), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  assert.ok(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, unb64url(s), new TextEncoder().encode(`${h}.${c}`)));
});
