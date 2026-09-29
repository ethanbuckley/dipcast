// Prints a new VAPID key pair. Run: node scripts/vapid-keys.mjs
// It prints and writes nothing to disk, so the private key exists only in your terminal
// until you paste it into `npx wrangler secret put VAPID_PRIVATE_KEY`.

import { b64url } from '../src/webpush.js';

const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
const raw = new Uint8Array(await crypto.subtle.exportKey('raw', publicKey));
const { d } = await crypto.subtle.exportKey('jwk', privateKey);

console.log(`VAPID_PUBLIC_KEY=${b64url(raw)}`);
console.log(`VAPID_PRIVATE_KEY=${d}`);
