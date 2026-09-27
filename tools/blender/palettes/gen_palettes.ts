// Mirror the Lospec palette table into the Blender addon as a Python module.
//
//   bun run tools/blender/palettes/gen_palettes.ts
//
// The addon folder is what gets junctioned into Blender's addon dirs, so it
// cannot reach back out to this folder at runtime -- the data has to LIVE in
// the package. This keeps picocad_palettes.ts the one place a palette is
// authored: add one there, re-run this, and the dropdown grows.
import { writeFileSync } from 'node:fs';
import { picoCadPalettes } from './picocad_palettes.ts';

const OUT = new URL('../pc2_entity/palettes.py', import.meta.url);

const py = (v: unknown): string =>
    typeof v === 'string' ? JSON.stringify(v)
    : Array.isArray(v) ? `(${v.map(py).join(', ')}${v.length === 1 ? ',' : ''})`
    : String(v);

const entries = Object.values(picoCadPalettes).map((p) => `    {
        "id": ${py(p.id)},
        "name": ${py(p.name)},
        "author": ${py(p.author)},
        "url": ${py(p.sourceUrl)},
        "colors": ${py(p.colors as unknown as number[][])},
        "shade1": ${py(p.shadePal1 as unknown as number[])},
        "shade2": ${py(p.shadePal2 as unknown as number[])},
    },`);

writeFileSync(OUT, `"""16-colour palettes to preview an entity under. GENERATED -- do not hand-edit.

Written by tools/blender/palettes/gen_palettes.ts from picocad_palettes.ts,
which is where a palette is added. Colours are sRGB 0..255 triples as Lospec
publishes them; the addon divides by 255, because model.txt's own palette is
0..1 floats and the swatches have to speak one unit.

The shade tables ride along unused: nothing in the addon shades, but a palette
without them is not a picoCAD2 palette, and this file is meant to be a faithful
copy rather than only the part today needs.
"""

PALETTES = (
${entries.join('\n')}
)
`);
console.log(`${OUT.pathname}: ${Object.keys(picoCadPalettes).length} palettes`);
