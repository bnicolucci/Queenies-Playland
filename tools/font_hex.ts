// Glyph hex for the HUD font in src/main.js, cut out of tools/engineFont.png.
//
//   bun run tools/font_hex.ts "0123456789CLIKSQUEN'PAYD"    # the shipped set
//   bun run tools/font_hex.ts --missing                     # letters not in it
//
// The sheet is a 256x24 1-bit PNG: 96 ASCII glyphs (32..127) in 8x8 cells,
// 32 columns by 3 rows. A glyph is 8 bytes, one per row, MSB = leftmost pixel,
// so 16 hex chars -- which is the shape main.js's FONT string wants. Uppercase
// ink is 7x7: COLUMN 7 IS BLANK IN EVERY GLYPH (checked below), which is what
// lets the atlas pack at a 7px pitch if the glyph count ever needs it. Only Q
// uses row 7, for its tail.
import { readFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';

const buf = readFileSync(new URL('./engineFont.png', import.meta.url));
let p = 8, w = 0, h = 0, bd = 0;
const idat: Buffer[] = [];
let trns: Buffer | null = null;
while (p < buf.length) {
  const len = buf.readUInt32BE(p), type = buf.toString('ascii', p + 4, p + 8);
  const d = buf.subarray(p + 8, p + 8 + len);
  if (type === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); bd = d[8]; }
  if (type === 'tRNS') trns = Buffer.from(d);
  if (type === 'IDAT') idat.push(Buffer.from(d));
  p += 12 + len;
}
if (bd !== 1) throw new Error(`expected a 1-bit sheet, got ${bd}-bit`);
const raw = inflateSync(Buffer.concat(idat)), rowBytes = Math.ceil(w * bd / 8);
const px = Buffer.alloc(h * rowBytes);
let pos = 0;
for (let y = 0; y < h; y++) {                      // undo the per-row PNG filter
  const f = raw[pos++], line = raw.subarray(pos, pos + rowBytes); pos += rowBytes;
  const cur = px.subarray(y * rowBytes, (y + 1) * rowBytes);
  const prev = y > 0 ? px.subarray((y - 1) * rowBytes, y * rowBytes) : Buffer.alloc(rowBytes);
  for (let i = 0; i < rowBytes; i++) {
    const a = i >= 1 ? cur[i - 1] : 0, b = prev[i], c = i >= 1 ? prev[i - 1] : 0;
    let v = line[i];
    if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
    else if (f === 4) {
      const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c);
      v += (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
    }
    cur[i] = v & 255;
  }
}
// palette index 0 is the transparent one, so "lit" is any opaque texel
const lit = (x: number, y: number) => {
  const i = (px[y * rowBytes + (x >> 3)] >> (7 - (x & 7))) & 1;
  return (trns && i < trns.length ? trns[i] : 255) >= 128;
};
const rowsOf = (ch: string) => {
  const g = ch.charCodeAt(0) - 32;
  if (g < 0 || g > 95) throw new Error(`${JSON.stringify(ch)} is outside the sheet`);
  const gx = (g % 32) * 8, gy = (g / 32 | 0) * 8, out: number[] = [];
  for (let y = 0; y < 8; y++) {
    let b = 0;
    for (let x = 0; x < 8; x++) if (lit(gx + x, gy + y)) b |= 1 << (7 - x);
    out.push(b);
  }
  return out;
};
const hex = (s: string) => [...s].map(c => rowsOf(c).map(b => b.toString(16).padStart(2, '0')).join('')).join('');

const SHIPPED = "0123456789CLIKSQUEN'PAYD";
const arg = process.argv[2];
if (arg === '--missing') {
  const miss = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'].filter(c => !SHIPPED.includes(c)).join('');
  console.log(`not in the shipped set (${miss.length}): ${[...miss].join(' ')}`);
  console.log(`room to the 32-glyph cap: ${32 - SHIPPED.length}\n`);
  for (const c of miss) console.log(`${c}  ${hex(c)}`);
} else {
  const set = arg ?? SHIPPED;
  console.log(`${set.length} glyphs, ${set.length * 16} hex chars\n${hex(set)}`);
  for (const c of set) {
    const r = rowsOf(c);
    console.log(`\n${c}`);
    for (const b of r) console.log('  ' + [...Array(8)].map((_, i) => (b >> (7 - i) & 1) ? '#' : '.').join(''));
  }
}
