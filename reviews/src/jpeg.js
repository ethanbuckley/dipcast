// Photos arrive as JPEG files the page has already shrunk and saved again from a canvas, which
// leaves the camera's metadata behind. A client need not be the page, so the Worker checks that a
// file is a JPEG, reads its size, and copies it without the segments that can carry metadata:
// APP1 (Exif, with any GPS position, and XMP), APP3 to APP13, APP15 and comments. It keeps APP0
// (JFIF), APP2 (the ICC colour profile) and APP14 (Adobe's colour transform), which change how the
// picture looks. Only the segments before the image data are read: metadata sits there in every
// file a camera or a browser writes, and a scan of the image data itself would cost more CPU time
// than a free-plan request has (10 ms).

export class BadImage extends Error {}

// Start of frame: baseline, progressive, lossless and the arithmetic-coded kinds, never DHT (C4),
// JPG (C8) or DAC (CC), which share the range.
const SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
const KEEP_APP = new Set([0xe0, 0xe2, 0xee]);
const SOS = 0xda, COM = 0xfe;

// Returns { bytes, width, height }; throws BadImage for anything that is not a well-formed JPEG
// up to its image data.
export function cleanJpeg(input) {
  const b = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8 || b[2] !== 0xff) throw new BadImage('not a JPEG');
  const keep = [b.subarray(0, 2)];
  let i = 2, width = 0, height = 0;
  for (;;) {
    if (i + 4 > b.length) throw new BadImage('the JPEG ends before its image data');
    if (b[i] !== 0xff) throw new BadImage('the JPEG is malformed');
    const marker = b[i + 1];
    if (marker === 0xff) { i++; continue; }   // a fill byte before a marker
    if (marker === 0x00 || marker === 0x01 || marker === 0xd8 || marker === 0xd9 || (marker >= 0xd0 && marker <= 0xd7)) {
      throw new BadImage('the JPEG is malformed');   // no segment of these belongs before the image data
    }
    const length = (b[i + 2] << 8) | b[i + 3], end = i + 2 + length;
    if (length < 2) throw new BadImage('the JPEG is malformed');
    if (end > b.length) throw new BadImage('the JPEG ends before its image data');
    if (marker === SOS) {
      if (!width || !height) throw new BadImage('the JPEG has no frame size');
      keep.push(b.subarray(i));   // the image data and everything after it, as it came
      break;
    }
    if (SOF.has(marker)) {
      if (length < 8) throw new BadImage('the JPEG is malformed');
      height = (b[i + 5] << 8) | b[i + 6];
      width = (b[i + 7] << 8) | b[i + 8];
    }
    const metadata = (marker >= 0xe0 && marker <= 0xef && !KEEP_APP.has(marker)) || marker === COM;
    if (!metadata) keep.push(b.subarray(i, end));
    i = end;
  }
  const bytes = new Uint8Array(keep.reduce((n, part) => n + part.length, 0));
  let at = 0;
  for (const part of keep) { bytes.set(part, at); at += part.length; }
  return { bytes, width, height };
}
