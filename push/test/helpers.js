import { createDecipheriv, createECDH, createHmac, randomBytes } from 'node:crypto';

// Workers KV, as far as this Worker uses it. A small page size exercises the list cursor.
export class FakeKV {
  constructor(pageSize = 2) { this.map = new Map(); this.meta = new Map(); this.pageSize = pageSize; this.writes = 0; this.gets = 0; }
  async get(key, type) {
    this.gets++;
    const value = this.map.get(key);
    if (value === undefined) return null;
    return (type?.type ?? type) === 'json' ? JSON.parse(value) : value;
  }
  async put(key, value, { metadata } = {}) {
    if (typeof value !== 'string') throw new TypeError('FakeKV stores strings only');
    if (metadata !== undefined && JSON.stringify(metadata).length > 1024) throw new Error('metadata over 1024 bytes');
    this.writes++;
    this.map.set(key, value);
    if (metadata === undefined) this.meta.delete(key); else this.meta.set(key, metadata);
  }
  async delete(key) { this.writes++; this.map.delete(key); this.meta.delete(key); }
  async list({ prefix = '', cursor } = {}) {
    const names = [...this.map.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const end = Math.min(start + this.pageSize, names.length);
    const keys = names.slice(start, end).map((name) => (this.meta.has(name) ? { name, metadata: this.meta.get(name) } : { name }));
    return end < names.length ? { keys, list_complete: false, cursor: String(end) } : { keys, list_complete: true };
  }
}

const hmac = (key, ...data) => {
  const h = createHmac('sha256', key);
  for (const d of data) h.update(d);
  return h.digest();
};

// The user agent's side of RFC 8291 section 3.4 and RFC 8188 section 2, written with
// node:crypto (not WebCrypto) so that it shares no code with src/webpush.js.
export function decrypt(body, uaEcdh, authSecret) {
  body = Buffer.from(body);
  const salt = body.subarray(0, 16);
  const rs = body.readUInt32BE(16);
  const idlen = body[20];
  const asPublic = body.subarray(21, 21 + idlen);
  const record = body.subarray(21 + idlen);
  if (record.length > rs) throw new Error('more than one record');

  const uaPublic = uaEcdh.getPublicKey();
  const prkKey = hmac(authSecret, uaEcdh.computeSecret(asPublic));
  const ikm = hmac(prkKey, Buffer.from('WebPush: info\0'), uaPublic, asPublic, Buffer.from([1]));
  const prk = hmac(salt, ikm);
  const cek = hmac(prk, Buffer.from('Content-Encoding: aes128gcm\0\x01')).subarray(0, 16);
  const nonce = hmac(prk, Buffer.from('Content-Encoding: nonce\0\x01')).subarray(0, 12);

  const decipher = createDecipheriv('aes-128-gcm', cek, nonce);
  decipher.setAuthTag(record.subarray(-16));
  const padded = Buffer.concat([decipher.update(record.subarray(0, -16)), decipher.final()]);
  let end = padded.length - 1;
  while (end >= 0 && padded[end] === 0) end--;
  if (padded[end] !== 2) throw new Error('last record must end with the 0x02 delimiter');
  return padded.subarray(0, end);
}

// A browser: a subscription plus the private half needed to read what is pushed to it.
export function makeUserAgent(endpoint) {
  const ecdh = createECDH('prime256v1');
  ecdh.generateKeys();
  const auth = randomBytes(16);
  return {
    subscription: {
      endpoint,
      expirationTime: null,
      keys: { p256dh: ecdh.getPublicKey().toString('base64url'), auth: auth.toString('base64url') },
    },
    read: (body) => JSON.parse(decrypt(body, ecdh, auth).toString('utf8')),
  };
}

export async function makeVapidEnv(subject = 'mailto:owner@example.com') {
  const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const raw = Buffer.from(await crypto.subtle.exportKey('raw', publicKey));
  const { d } = await crypto.subtle.exportKey('jwk', privateKey);
  return { VAPID_PUBLIC_KEY: raw.toString('base64url'), VAPID_PRIVATE_KEY: d, VAPID_SUBJECT: subject };
}
