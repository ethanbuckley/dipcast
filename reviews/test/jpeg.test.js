import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BadImage, cleanJpeg } from '../src/jpeg.js';
import { hasBytes, jpeg } from './helpers.js';

test('the camera metadata goes: Exif with its GPS position, XMP and comments', () => {
  const input = jpeg({ exif: true, xmp: true, comment: true, icc: true });
  const { bytes, width, height } = cleanJpeg(input);
  assert.deepEqual([width, height], [1280, 960]);
  for (const gone of ['Exif', 'GPSLatitude', 'xmpmeta', 'Acacia Avenue']) assert.ok(!hasBytes(bytes, gone), gone);
  // JFIF, tables, frame and scan stay; untrusted colour profiles go.
  assert.ok(!hasBytes(bytes, 'ICC_PROFILE'));
  for (const kept of ['JFIF']) assert.ok(hasBytes(bytes, kept), kept);
  const plain = cleanJpeg(jpeg({ icc: true })).bytes;
  assert.deepEqual(bytes, plain, 'the same file as one that never had the metadata');
});

test('a real camera file (written by Pillow, with a GPS position) loses its Exif and keeps its size', () => {
  const input = readFileSync(new URL('./fixtures/camera.jpg', import.meta.url));
  assert.ok(hasBytes(input, 'Exif') && hasBytes(input, 'Phone maker') && hasBytes(input, 'a comment'));
  const { bytes, width, height } = cleanJpeg(input);
  assert.deepEqual([width, height], [48, 36]);
  for (const gone of ['Exif', 'Phone maker', 'a comment']) assert.ok(!hasBytes(bytes, gone), gone);
  assert.ok(bytes.length < input.length);
  assert.deepEqual([bytes[bytes.length - 2], bytes[bytes.length - 1]], [0xff, 0xd9], 'the image data and its end are kept');
});

test('the size of a progressive JPEG, and fill bytes before a marker', () => {
  assert.deepEqual((({ width, height }) => [width, height])(cleanJpeg(jpeg({ width: 720, height: 1280, progressive: true }))), [720, 1280]);
  const padded = jpeg();
  const withFill = new Uint8Array([...padded.subarray(0, 2), 0xff, ...padded.subarray(2)]);
  assert.equal(cleanJpeg(withFill).width, 1280);
});

test('anything else is refused', () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.throws(() => cleanJpeg(png), BadImage);
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8])), BadImage);
  const full = jpeg();
  assert.throws(() => cleanJpeg(full.subarray(0, 30)), /ends early/);
  assert.throws(() => cleanJpeg(full.subarray(0, full.length - 2)), /ends early/, 'no end-of-image marker');
  // A scan before any frame says how big the picture is.
  const noFrame = new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x08, 1, 1, 0, 0, 63, 0, 0xff, 0xd9]);
  assert.throws(() => cleanJpeg(noFrame), /no frame size/);
  // A segment that claims to run past the end of the file, and one too short to hold its length.
  const long = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x40, 0x00, 1, 2, 3]);
  assert.throws(() => cleanJpeg(long), /ends early/);
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x01, 1, 2])), /malformed/);
  // An end before any image data.
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, 0xff, 0xd9, 0, 0])), /no image data/);
});

// The five ways the code review got a GPS position past the first version, which read only up to the
// first scan: each must now come out without it.
const GPS = 'GPSLatitude 54.5732N';
const ascii = (t) => Array.from(t, (c) => c.charCodeAt(0));
const seg = (marker, body) => [0xff, marker, (body.length + 2) >> 8, (body.length + 2) & 0xff, ...body];
const exif = seg(0xe1, [...ascii('Exif'), 0, 0, ...ascii(GPS)]);
// Splits a test JPEG at its end-of-image marker, to put things before or after it.
const body = () => { const j = [...jpeg({ progressive: true })]; return [j.slice(0, -2), [0xff, 0xd9]]; };
const scan = [...seg(0xda, [1, 1, 0, 1, 5, 0]), 0x21, 0x43, 0xff, 0x00];

test('Exif between progressive scans is dropped, and the scans kept', () => {
  const [head, eoi] = body();
  const { bytes } = cleanJpeg(new Uint8Array([...head, ...exif, ...scan, ...eoi]));
  assert.ok(!hasBytes(bytes, GPS) && !hasBytes(bytes, 'Exif'));
  assert.deepEqual([...bytes.subarray(-scan.length - 2)], [...scan, 0xff, 0xd9], 'the second scan is still there');
});

test('nothing after the end of the image is kept: a motion clip, a second picture', () => {
  const [head, eoi] = body();
  const { bytes } = cleanJpeg(new Uint8Array([...head, ...eoi, 0xff, 0xd8, ...exif, 0xff, 0xd9, ...ascii('ftypmp42 ' + GPS)]));
  assert.ok(!hasBytes(bytes, GPS));
  assert.deepEqual([...bytes.subarray(-2)], [0xff, 0xd9]);
});

test('APP2 metadata is dropped, including untrusted colour profiles and MPF', () => {
  const [head, eoi] = body();
  const mpf = seg(0xe2, [...ascii('MPF'), 0, ...ascii(GPS)]);
  const icc = seg(0xe2, [...ascii('ICC_PROFILE'), 0, 1, 1, ...ascii('Display P3')]);
  const { bytes } = cleanJpeg(new Uint8Array([0xff, 0xd8, ...mpf, ...icc, ...head.slice(2), ...eoi]));
  assert.ok(!hasBytes(bytes, GPS) && !hasBytes(bytes, 'MPF') && !hasBytes(bytes, 'Display P3'));
});

test('a marker a photo does not need is refused', () => {
  const [head, eoi] = body();
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, ...seg(0x02, ascii(GPS)), ...head.slice(2), ...eoi])), /a marker a photo does not need/);
});

test('a table whose size does not add up is refused: no bytes hidden inside one', () => {
  const [head, eoi] = body();
  const padded = seg(0xdb, [0, ...new Array(64).fill(1), ...exif]);   // a 64-value table, then more
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, ...padded, ...head.slice(2), ...eoi])), /malformed/);
  const dht = seg(0xc4, [0x00, 1, ...new Array(15).fill(0), 7, ...ascii('xx')]);   // one code, two values
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, ...dht, ...head.slice(2), ...eoi])), /malformed/);
});

test('a JFIF header with a thumbnail of its own goes; a plain one stays', () => {
  const [head, eoi] = body();
  const withThumb = seg(0xe0, [...ascii('JFIF'), 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 9, 9, 9]);
  const { bytes } = cleanJpeg(new Uint8Array([0xff, 0xd8, ...withThumb, ...head.slice(2), ...eoi]));
  assert.equal(bytes.filter((x, k) => x === 0xff && bytes[k + 1] === 0xe0).length, 1, 'only the plain JFIF header the test JPEG has');
});


test('location text in an ICC profile or after an Adobe header cannot survive', () => {
  const camera = readFileSync(new URL('./fixtures/camera.jpg', import.meta.url));
  const adobe = [65, 100, 111, 98, 101, 0, 100, 0, 0, 0, 0, 1];
  for (const [marker, prefix] of [[0xe2, [...ascii('ICC_PROFILE'), 0, 1, 1]], [0xee, adobe]]) {
    const input = new Uint8Array([255, 216, ...seg(marker, [...prefix, ...ascii(GPS)]), ...camera.subarray(2)]);
    const { bytes, width, height } = cleanJpeg(input);
    assert.ok(!hasBytes(bytes, GPS));
    assert.deepEqual([width, height], [48, 36]);
    if (marker === 0xee) assert.ok(Buffer.from(bytes).includes(Buffer.from(seg(marker, adobe))), 'the colour transform remains');
  }
});
