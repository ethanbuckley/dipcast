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
  // What changes how the picture looks stays: JFIF, the colour profile, the tables, frame and scan.
  for (const kept of ['JFIF', 'ICC_PROFILE']) assert.ok(hasBytes(bytes, kept), kept);
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
  assert.throws(() => cleanJpeg(full.subarray(0, 30)), /ends before its image data/);
  // A scan before any frame says how big the picture is.
  const noFrame = new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x08, 1, 1, 0, 0, 63, 0, 0xff, 0xd9]);
  assert.throws(() => cleanJpeg(noFrame), /no frame size/);
  // A segment that claims to run past the end of the file, and one too short to hold its length.
  const long = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x40, 0x00, 1, 2, 3]);
  assert.throws(() => cleanJpeg(long), /ends before its image data/);
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x01, 1, 2])), /malformed/);
  // A marker that cannot come before the image data.
  assert.throws(() => cleanJpeg(new Uint8Array([0xff, 0xd8, 0xff, 0xd9, 0, 0])), /malformed/);
});
