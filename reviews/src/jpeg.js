// Photos arrive as JPEG files the page has already shrunk and saved again from a canvas, which
// leaves the camera's metadata behind. A client need not be the page, so the Worker rebuilds every
// photo from the parts a picture needs and nothing else, through to its end-of-image marker:
//
// - kept: the frame (SOF), the scans (SOS and their image data), and the tables they use (DQT, DHT,
//   DAC, DRI), each checked to be exactly the size its contents say; APP0 when it is a plain JFIF
//   header and a rebuilt APP14 Adobe colour transform;
// - dropped, wherever they are, before the image data or between progressive scans: every other APPn
//   (Exif with its GPS position, XMP, MPF, maker notes, ICC profiles) and every comment. ICC profiles
//   can contain arbitrary text; the page already converts photos to sRGB on its canvas;
// - dropped: anything after the end-of-image marker (a phone's motion clip, a second picture);
// - refused: any other marker, a table whose size does not add up, and a file with no end.
//
// One pass, jumping between 0xFF bytes in the image data with indexOf, so a 1.5 MB photo costs far
// less than the 10 ms of CPU a free-plan request has.

export class BadImage extends Error {}

// Start of frame: baseline, progressive, lossless and the arithmetic-coded kinds; not DHT (C4),
// JPG (C8) or DAC (CC), which share the range.
const SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
const DHT = 0xc4, DAC = 0xcc, DQT = 0xdb, DRI = 0xdd, SOS = 0xda, EOI = 0xd9, COM = 0xfe;
const isApp = (m) => m >= 0xe0 && m <= 0xef;
const startsWith = (b, at, text) => [...text].every((c, i) => b[at + i] === c.charCodeAt(0));

// The segment's payload checked against what its contents say its length must be.
function checkTables(marker, b, start, end) {
  let i = start;
  if (marker === DQT) {
    while (i < end) { const precision = b[i] >> 4, id = b[i] & 15; if (precision > 1 || id > 3) return false; i += 1 + (precision ? 128 : 64); }
  } else if (marker === DHT) {
    while (i < end) {
      if (i + 17 > end || (b[i] >> 4) > 1 || (b[i] & 15) > 3) return false;
      let count = 0;
      for (let k = 1; k <= 16; k++) count += b[i + k];
      if (count > 256) return false;
      i += 17 + count;
    }
  } else if (marker === DAC) {
    i += (end - start) % 2 ? Infinity : end - start;
  } else if (marker === DRI) {
    i += end - start === 2 ? 2 : Infinity;
  } else if (SOF.has(marker)) {
    const n = b[start + 5];
    i += n >= 1 && n <= 4 && end - start === 6 + 3 * n ? end - start : Infinity;
  } else if (marker === SOS) {
    const n = b[start];
    i += n >= 1 && n <= 4 && end - start === 4 + 2 * n ? end - start : Infinity;
  }
  return i === end;
}

// Keep only a plain JFIF header and the fields needed for Adobe's colour transform. Rebuild
// Adobe from known fields so neither trailing bytes nor arbitrary header flags survive.
function cleanApp(marker, b, start, end) {
  if (marker === 0xe0 && end - start === 14 && startsWith(b, start, 'JFIF\0') && b[start + 12] === 0 && b[start + 13] === 0) {
    return b.subarray(start, end);
  }
  if (marker === 0xee && end - start >= 12 && startsWith(b, start, 'Adobe') && b[start + 11] <= 2) {
    return new Uint8Array([65, 100, 111, 98, 101, 0, 100, 0, 0, 0, 0, b[start + 11]]);
  }
  return null;
}

// Returns { bytes, width, height }; throws BadImage for anything that is not a well-formed JPEG.
export function cleanJpeg(input) {
  const b = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8 || b[2] !== 0xff) throw new BadImage('not a JPEG');
  const keep = [b.subarray(0, 2)];
  let i = 2, width = 0, height = 0, scans = 0;
  for (;;) {
    // At a marker: before the first scan directly, between scans after the image data (below).
    if (i + 2 > b.length) throw new BadImage('the JPEG ends early');
    if (b[i] !== 0xff) throw new BadImage('the JPEG is malformed');
    const marker = b[i + 1];
    if (marker === 0xff) { i++; continue; }   // a fill byte before a marker
    if (marker === EOI) {
      if (!scans) throw new BadImage('the JPEG has no image data');
      keep.push(b.subarray(i, i + 2));   // and nothing after it
      break;
    }
    if (i + 4 > b.length) throw new BadImage('the JPEG ends early');
    const length = (b[i + 2] << 8) | b[i + 3], start = i + 4, end = i + 2 + length;
    if (length < 2) throw new BadImage('the JPEG is malformed');
    if (end > b.length) throw new BadImage('the JPEG ends early');
    if (isApp(marker) || marker === COM) {
      const payload = isApp(marker) ? cleanApp(marker, b, start, end) : null;
      if (payload) keep.push(new Uint8Array([0xff, marker, 0, payload.length + 2]), payload);
      i = end;
      continue;
    }
    if (!(SOF.has(marker) || marker === DHT || marker === DAC || marker === DQT || marker === DRI || marker === SOS)) {
      throw new BadImage(`the JPEG has a marker a photo does not need (0x${marker.toString(16)})`);
    }
    if (!checkTables(marker, b, start, end)) throw new BadImage('the JPEG is malformed');
    if (SOF.has(marker)) {
      if (width) throw new BadImage('the JPEG has two frames');
      height = (b[start + 1] << 8) | b[start + 2];
      width = (b[start + 3] << 8) | b[start + 4];
    }
    keep.push(b.subarray(i, end));
    i = end;
    if (marker !== SOS) continue;
    if (!width || !height) throw new BadImage('the JPEG has no frame size');
    scans++;
    // The image data runs to the next marker: an 0xFF not followed by 0x00 (a stuffed byte), a
    // restart marker (D0 to D7, part of the data) or another 0xFF (fill).
    const from = i;
    for (;;) {
      i = b.indexOf(0xff, i);
      if (i < 0 || i + 1 >= b.length) throw new BadImage('the JPEG ends early');
      const next = b[i + 1];
      if (next === 0x00 || (next >= 0xd0 && next <= 0xd7)) { i += 2; continue; }
      if (next === 0xff) { i++; continue; }
      break;
    }
    keep.push(b.subarray(from, i));
  }
  const bytes = new Uint8Array(keep.reduce((n, part) => n + part.length, 0));
  let at = 0;
  for (const part of keep) { bytes.set(part, at); at += part.length; }
  return { bytes, width, height };
}
