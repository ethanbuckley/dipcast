// Web Push from the RFCs, WebCrypto only, so the same code runs in a Worker and in Node:
// message encryption (RFC 8291) with the aes128gcm content coding (RFC 8188),
// and sender identification with VAPID (RFC 8292).

const te = new TextEncoder();
const ECDH = { name: 'ECDH', namedCurve: 'P-256' };
const ECDSA = { name: 'ECDSA', namedCurve: 'P-256' };
const RECORD_SIZE = 4096;
// RFC 8291 section 4: a 4096-byte body leaves 3993 bytes of plaintext after the
// 86-byte header, the padding delimiter and the 16-byte tag.
const MAX_PLAINTEXT = 3993;

export function b64url(bytes) {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function unb64url(str) {
  if (typeof str !== 'string' || !/^[A-Za-z0-9_-]*={0,2}$/.test(str)) throw new Error('not base64url');
  const s = str.replace(/=+$/, '').replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(s + '==='.slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
}

export function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

// WebCrypto cannot import a raw private scalar, so build a JWK from the public point and d.
export function ecJwk(publicRaw, d) {
  if (publicRaw.length !== 65 || publicRaw[0] !== 4) throw new Error('public key must be a 65-byte uncompressed P-256 point');
  const jwk = { kty: 'EC', crv: 'P-256', x: b64url(publicRaw.subarray(1, 33)), y: b64url(publicRaw.subarray(33)) };
  if (d !== undefined) jwk.d = typeof d === 'string' ? d : b64url(d);
  return jwk;
}

export const importEcdhPublic = (raw) => crypto.subtle.importKey('raw', raw, ECDH, true, []);

async function hkdf(salt, ikm, info, bytes) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, bytes * 8));
}

// keys: the subscription's {p256dh, auth}. salt and senderKeys are injectable only so
// tests can reproduce RFC 8291 Appendix A; in use both must be fresh for every message.
export async function encrypt(plaintext, keys, { salt, senderKeys } = {}) {
  if (plaintext.length > MAX_PLAINTEXT) throw new Error('payload too large');
  const uaPublic = unb64url(keys.p256dh);
  const authSecret = unb64url(keys.auth);
  salt ??= crypto.getRandomValues(new Uint8Array(16));
  senderKeys ??= await crypto.subtle.generateKey(ECDH, false, ['deriveBits']);
  const asPublic = new Uint8Array(await crypto.subtle.exportKey('raw', senderKeys.publicKey));
  const shared = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'ECDH', public: await importEcdhPublic(uaPublic) }, senderKeys.privateKey, 256));

  const ikm = await hkdf(authSecret, shared, concat(te.encode('WebPush: info\0'), uaPublic, asPublic), 32);
  const cek = await hkdf(salt, ikm, te.encode('Content-Encoding: aes128gcm\0'), 16);
  const nonce = await hkdf(salt, ikm, te.encode('Content-Encoding: nonce\0'), 12);

  // A single record is also the last one, so it ends with the 0x02 delimiter and no padding.
  const aes = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, aes, concat(plaintext, [2])));

  const header = new Uint8Array(21);
  header.set(salt);
  new DataView(header.buffer).setUint32(16, RECORD_SIZE);
  header[20] = asPublic.length;
  return concat(header, asPublic, sealed);
}

// Returns endpoint => Authorization header. One JWT per push service origin is reused
// for the whole run, which saves a signature per subscriber.
export function vapidSigner({ VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT }, now = Date.now) {
  if (!/^(mailto:|https:)/.test(VAPID_SUBJECT ?? '')) throw new Error('VAPID_SUBJECT must be a mailto: or https: URL');
  if (!VAPID_PRIVATE_KEY) throw new Error('VAPID_PRIVATE_KEY is not set');
  const jwk = ecJwk(unb64url(VAPID_PUBLIC_KEY), VAPID_PRIVATE_KEY);
  let key;
  const byAudience = new Map();
  const part = (obj) => b64url(te.encode(JSON.stringify(obj)));

  async function sign(aud) {
    key ??= crypto.subtle.importKey('jwk', jwk, ECDSA, false, ['sign']);
    // exp must be under 24 h (RFC 8292 section 2); 12 h leaves room for clock skew.
    const claims = { aud, exp: Math.floor(now() / 1000) + 12 * 3600, sub: VAPID_SUBJECT };
    const unsigned = `${part({ typ: 'JWT', alg: 'ES256' })}.${part(claims)}`;
    // WebCrypto's ECDSA output is already the raw r||s form that JWS ES256 uses.
    const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, await key, te.encode(unsigned));
    return `vapid t=${unsigned}.${b64url(new Uint8Array(sig))}, k=${VAPID_PUBLIC_KEY}`;
  }

  return (endpoint) => {
    const aud = new URL(endpoint).origin;
    if (!byAudience.has(aud)) byAudience.set(aud, sign(aud));
    return byAudience.get(aud);
  };
}

export async function sendPush(subscription, payload, authorize, { fetch = globalThis.fetch, ttl = 43200, urgency = 'normal', signal = AbortSignal.timeout(15000) } = {}) {
  const body = await encrypt(te.encode(JSON.stringify(payload)), subscription.keys);
  return fetch(subscription.endpoint, {
    method: 'POST',
    signal,
    headers: {
      'Content-Encoding': 'aes128gcm',
      'Content-Type': 'application/octet-stream',
      TTL: String(ttl),
      Urgency: urgency,
      Authorization: await authorize(subscription.endpoint),
    },
    body,
  });
}
