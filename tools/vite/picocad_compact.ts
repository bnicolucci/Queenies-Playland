import { readFile } from 'node:fs/promises';
import type { Plugin } from 'vite';
// The runtime's texture width, imported rather than re-typed: this encoder
// writes UVs in texels and src/pico.js divides by the same number.
import { TEX_W } from '../../src/pico.js';

// Build-time model compaction, ported from picocad2-js13k's
// tools/vite/picocad_compact.ts: any bundled `*.txt?raw` that parses as a
// picoCAD2 model (texture + graph) is re-encoded as positional tuples with
// bit-packed face flags and rounded floats. src/pico.js decodes the `pc2!`
// prefix transparently at runtime. Dev serves the raw files untouched
// (apply: 'build'), so the two can never disagree about the raw format.
//
// The tuples carry only what the runtime reads back: picoCAD's `shading_mode`
// used to ride along here and in the decoder, but main.js forces shading on
// regardless, so nothing ever consumed it. Keep it that way — a field the
// parser exposes but no caller reads is payload the zip pays for.
//
// Texture pixels are deliberately NOT packed — they go out as the authored one
// hex digit per pixel. The original measured packing 2-per-byte + base64: the
// string halves but the final zip GROWS by 328 bytes, because a picoCAD
// texture is long runs of 16 symbols that Roadroller compresses ~30:1, and
// packing destroys the byte alignment those patterns rely on. Re-measure with
// `bun run pack` before "optimizing" this.

const PREFIX = 'pc2!';

// 1e8 keeps positions exact to 0.00000001 units. 1e5 was NOT enough, and the
// reason is that a vertex error is AMPLIFIED by every scale above it: a part
// scaled 14.6 turns 5e-6 into 7e-5, which is enough to move a pixel sitting on
// a shading-band or silhouette boundary onto the far side of it. Measured
// against dev, which serves the raw model and so never rounds -- 1e5 gave 9/12
// identical frames, 1e6 11/12, 1e7 12/12 -- and it is nearly free, because the
// authored coordinates are mostly short decimals already, so the extra digits
// only reach the handful of values that were genuinely irrational: 1e7 costs
// 15 zip bytes where dropping the rounding entirely costs 235.
// The 600x800 fixed camera exposed 4-10 wrong pixels in seven frames at 1e7;
// 1e8 restores 12/12 identical dev/packed frames (+82 ZIP bytes on that scene).
const round = (v: number, p = 1e8): number => Math.round(v * p) / p;

// UVs go out in TEXELS, not texture fractions, and that is a CORRECTNESS fix
// before it is a size one. picoCAD paints on the texel grid, so almost every
// authored uv is an exact multiple of 1/TEX_W -- and rounding the FRACTION to
// 1e3 moved some of them across a texel boundary: 0.03125 (texel 4.0) rounds
// to 0.031 (3.968) and 0.28125 (36.0) to 0.281 (35.968), so a NEAREST sample
// picked the texel next door and drew a one-pixel line of it. That is
// invisible in dev, which serves the raw model, and only appears in the pack.
// In texels those same values are the integers 4 and 36, exact and shorter to
// write; 1e2 leaves 0.01 texel for the handful of unwrapped values that are
// not on the grid.
const uv = (u: number): number => round(u * TEX_W, 1e2);

type Json = any;

// A vector collapses to 0 when every component is its default.
const vec = (v: Json, def: number): 0 | number[] =>
    !v || ['x', 'y', 'z'].every((k) => (v[k] ?? def) === def)
        ? 0
        : [round(v.x ?? def), round(v.y ?? def), round(v.z ?? def)];

// Node tuple: [visible, pos, rot, scale, mesh, children, name?]. Names are
// kept ONLY on mesh nodes (they survive as parsePicoCAD's per-object name);
// group-node names drop. Face flags match src/pico.js's vertex attribute
// layout: bit 0 = noshade, bit 1 = notex.
const node = (n: Json): Json[] => {
    const t: Json[] = [
        n.visible === false ? 0 : 1,
        vec(n.transform?.pos, 0),
        vec(n.transform?.rot, 0),
        vec(n.transform?.scale, 1),
        n.mesh
            ? [
                  n.mesh.vertices.map((v: number) => round(v)),
                  n.mesh.faces.map((f: Json) => [
                      f.vertex_ids,
                      f.uvs.map(uv),
                      f.color,
                      (f.noshade ? 1 : 0) | (f.notex ? 2 : 0),
                  ]),
              ]
            : 0,
        (n.children ?? []).map((c: Json) => node(c)),
    ];
    if (n.mesh && n.name) t.push(n.name);
    return t;
};

export function tryEncodeCompact(text: string): string | null {
    let d: Json;
    try {
        d = JSON.parse(text);
    } catch {
        return null;
    }
    if (!d?.texture?.pixels || !d.graph) return null;
    const t = d.texture;
    return (
        PREFIX +
        JSON.stringify([
            [
                t.pixels,
                // The palette rides as 96 hex chars rather than 48 floats.
                // Lossless: the runtime quantises with Math.round(v * 255)
                // anyway, and picoCAD writes colours as 14-digit decimals
                // (0.30196078431373 for 77/255), the highest-entropy run in
                // the file. Measured -57 zip bytes; hex-nibbling the two shade
                // tables the same way was measured too and LOSES 4, so they
                // stay integer arrays.
                t.colors.flat().map((v: number) => Math.round(v * 255).toString(16).padStart(2, '0')).join(''),
                t.transparent_color ?? 0,
                t.shade_pal_1,
                t.shade_pal_2,
                t.background_color ?? 0,
            ],
            node(d.graph),
        ])
    );
}

export function picocadCompact(): Plugin {
    return {
        name: 'picocad-compact',
        apply: 'build',
        enforce: 'pre',
        async load(id) {
            const m = id.match(/^(.*\.txt)\?raw$/);
            if (!m) return null;
            const encoded = tryEncodeCompact(await readFile(m[1], 'utf8'));
            return encoded === null ? null : `export default ${JSON.stringify(encoded)};`;
        },
    };
}
