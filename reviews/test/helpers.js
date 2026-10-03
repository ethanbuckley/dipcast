import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

// D1, as far as this Worker uses it, over a real SQLite database (D1 is SQLite), with the tables
// made by the same migration the real database gets.
export class FakeD1 {
  constructor() {
    this.db = new DatabaseSync(':memory:');
    this.db.exec(readFileSync(new URL('../migrations/0001_reviews.sql', import.meta.url), 'utf8'));
  }
  prepare(sql) { return new Statement(this.db, sql, []); }
  async batch(statements) {
    this.db.exec('BEGIN');
    try {
      const out = statements.map((s) => s.runNow());
      this.db.exec('COMMIT');
      return out;
    } catch (err) { this.db.exec('ROLLBACK'); throw err; }
  }
  rows(sql, ...args) { return this.db.prepare(sql).all(...args).map((r) => ({ ...r })); }
}

class Statement {
  constructor(db, sql, args) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args) {
    if (args.some((a) => a === undefined)) throw new TypeError('D1_TYPE_ERROR: undefined cannot be bound');
    return new Statement(this.db, this.sql, args);
  }
  runNow() { const r = this.db.prepare(this.sql).run(...this.args); return { success: true, meta: { changes: Number(r.changes) } }; }
  async first() { const r = this.db.prepare(this.sql).get(...this.args); return r ? { ...r } : null; }
  async all() { return { success: true, results: this.db.prepare(this.sql).all(...this.args).map((r) => ({ ...r })) }; }
  async run() { return this.runNow(); }
}

// Workers KV for photos: bytes in, bytes out.
export class FakeKV {
  constructor() { this.map = new Map(); this.writes = 0; }
  async put(key, value) {
    this.writes++;
    this.map.set(key, typeof value === 'string' ? new TextEncoder().encode(value) : new Uint8Array(value instanceof ArrayBuffer ? value : value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength)));
  }
  async get(key, type) {
    const v = this.map.get(key);
    if (v === undefined) return null;
    if (type === 'arrayBuffer') return v.slice().buffer;
    return new TextDecoder().decode(v);
  }
  async delete(key) { this.map.delete(key); }
}

// A JPEG's segments up to its image data, as a camera writes them: APP0 (JFIF), then APP1 with Exif
// (and a GPS position) and XMP if asked for, a comment, a quantisation table, the frame with its
// size, and a scan. Not a picture a decoder could draw, but every byte the Worker reads is real.
export function jpeg({ width = 1280, height = 960, exif = false, xmp = false, comment = false, icc = false, progressive = false } = {}) {
  const seg = (marker, body) => [0xff, marker, (body.length + 2) >> 8, (body.length + 2) & 0xff, ...body];
  const ascii = (s) => Array.from(s, (c) => c.charCodeAt(0));
  const bytes = [0xff, 0xd8];
  bytes.push(...seg(0xe0, [...ascii('JFIF'), 0, 1, 1, 0, 0, 1, 0, 1, 0, 0]));
  if (exif) bytes.push(...seg(0xe1, [...ascii('Exif'), 0, 0, ...ascii('MM'), 0, 42, ...ascii('GPSLatitude 54.5732N GPSLongitude 3.1491W')]));
  if (xmp) bytes.push(...seg(0xe1, [...ascii('http://ns.adobe.com/xap/1.0/'), 0, ...ascii('<x:xmpmeta>photographer</x:xmpmeta>')]));
  if (icc) bytes.push(...seg(0xe2, [...ascii('ICC_PROFILE'), 0, 1, 1, ...ascii('sRGB')]));
  if (comment) bytes.push(...seg(0xfe, ascii('taken at home, 12 Acacia Avenue')));
  bytes.push(...seg(0xdb, [0, ...new Array(64).fill(1)]));
  bytes.push(...seg(progressive ? 0xc2 : 0xc0, [8, height >> 8, height & 0xff, width >> 8, width & 0xff, 1, 1, 0x11, 0]));
  bytes.push(...seg(0xda, [1, 1, 0, 0, 63, 0]), 0x12, 0x34, 0xff, 0x00, 0x56, 0xff, 0xd9);
  return new Uint8Array(bytes);
}

export const hasBytes = (haystack, text) => Buffer.from(haystack).includes(Buffer.from(text));
