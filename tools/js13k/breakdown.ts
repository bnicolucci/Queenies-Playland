// Where the ZIP's bytes actually go.
//
//   bun run breakdown                 # by module
//   bun run breakdown src/main.js     # by region inside one module
//   bun run breakdown --model         # inside the compact model payload
//   bun run breakdown --floor         # what the package costs before any game
//
// Nothing here is estimated. Every number is measured the only way that means
// anything in a Roadroller build: CUT the piece out of the bundle and run the
// real pipeline on what is left (pipeline.ts — the same pinned flags and the
// same HTML/zip steps `bun run pack` uses, which is why the two agree). The
// drop in ZIP bytes is what that piece costs.
//
// Deleting a byte range mid-bundle leaves JS that could never run. That is
// fine, and it is the whole trick: nothing executes it, and Roadroller and
// zopfli only ever see text.
//
// Two things the method cannot give you, both worth knowing before anyone adds
// a column up:
//
//   - Every number is a MARGINAL cost. Pull one module out and everything else
//     compresses slightly worse, because they share one Roadroller context. The
//     module table sums to ~8,660 against a 10,048 package.
//   - There is a fixed floor (`--floor`, ~740 bytes) belonging to no module at
//     all: the ZIP headers, the HTML shell, and Roadroller's own decoder.
//
// Regions inside a module are derived FROM THE SOURCE rather than configured
// here: a file with `// --- banner ---` section comments splits on those, and
// one without splits per `export const NAME`. So the breakdown follows the file
// as it is edited instead of going stale against a list kept somewhere else.
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { DIRTY, TUNED, inline, run, zipOf } from './pipeline';

// Its own build directory: `bun run pack` empties dist13k/, so building there
// would delete a game.zip somebody may be about to ship.
const OUT = 'dist13k-size';
const TMP = `${OUT}/tmp`;

const args = process.argv.slice(2);
const wantModel = args.includes('--model');
const wantFloor = args.includes('--floor');
const target = args.find((a) => !a.startsWith('--'));

// The sourcemap is what makes a bundle byte attributable to a source file.
run('bunx', ['vite', 'build', '--config', 'vite.js13k.config.ts', '--outDir', OUT, '--sourcemap'], true);

mkdirSync(TMP, { recursive: true });

const baseHtml = readFileSync(`${OUT}/index.html`, 'utf8');
// The map's URL comment is build-only noise; dropping it makes the baseline
// here exactly the number `bun run pack` prints.
const js = readFileSync(`${OUT}/app.js`, 'utf8').replace(/\n?\/\/# sourceMappingURL=.*$/, '');

let packs = 0;
const zipSize = async (source: string): Promise<number> => {
    const src = `${TMP}/${packs}.js`;
    const dst = `${TMP}/${packs}.rr.js`;
    packs++;
    writeFileSync(src, source, 'utf8');
    run('bunx', ['roadroller', src, ...DIRTY, '-O0', ...TUNED, '-o', dst], true);
    return (await zipOf(inline(baseHtml, readFileSync(dst, 'utf8')))).length;
};

// Cut ranges (possibly overlapping, any order) out of the bundle.
const cut = (ranges: [number, number][]): string => {
    let out = '';
    let at = 0;
    for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
        if (start > at) out += js.slice(at, start);
        at = Math.max(at, end);
    }
    return out + js.slice(at);
};

const base = await zipSize(js);
const rows: [string, number, number][] = [];
// Ordered by what a piece COSTS, which is the question, not by how many
// characters it is — the two disagree wildly (the model is the biggest source
// and the third biggest cost). The model view keeps its authored order, since
// its rows nest.
const report = (heading: string, unit = 'raw', sorted = true): void => {
    if (sorted) rows.sort((a, b) => b[2] - a[2]);
    const w = Math.max(24, ...rows.map((r) => r[0].length));
    console.log(`\n  ${heading}   (baseline ${base}, bundle ${js.length})\n`);
    console.log('  ' + 'what'.padEnd(w) + unit.padStart(9) + 'zip'.padStart(8) + '   zip/char');
    for (const [name, chars, zip] of rows) {
        console.log(
            '  ' + name.padEnd(w) + String(chars).padStart(9) + String(zip).padStart(8) +
            `   ${chars ? (zip / chars).toFixed(2) : '-'}`,
        );
    }
    const sum = rows.reduce((total, r) => total + r[2], 0);
    console.log(`\n  sum ${sum} of ${base} — the rest is shared compression context and the fixed floor (--floor)`);
};

// --- sourcemap: which source owns which bundle bytes ------------------------
const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const decodeVLQ = (field: string): number[] => {
    const out: number[] = [];
    let value = 0;
    let shift = 0;
    for (const ch of field) {
        const digit = B64.indexOf(ch);
        value += (digit & 31) << shift;
        if (digit & 32) {
            shift += 5;
            continue;
        }
        out.push(value & 1 ? -(value >> 1) : value >> 1);
        value = 0;
        shift = 0;
    }
    return out;
};

// src/assets is a junction to picoCAD2's own folder, so the model's real path
// resolves OUTSIDE the repo. Name every source where it lives in the tree.
const SEP = /[\\/]/g;
const label = (path: string): string => {
    const p = path.replace(SEP, '/');
    const inSrc = /(?:^|\/)(src\/.*)$/.exec(p);
    if (inSrc) return inSrc[1];
    const asset = /(?:^|\/)assets\/(.*)$/.exec(p);
    return asset ? `src/assets/${asset[1]}` : p;
};

const map = JSON.parse(readFileSync(`${OUT}/app.js.map`, 'utf8'));
const sources: string[] = map.sources.map(label);
const lineStart = [0];
for (let i = 0; i < js.length; i++) if (js[i] === '\n') lineStart.push(i + 1);

// A segment owns the output bytes from where it starts to where the next one
// does, which is as fine-grained an answer as a sourcemap has.
type Segment = { at: number; source: number; line: number };
const segments: Segment[] = [];
{
    let source = 0;
    let line = 0;
    let column = 0;
    map.mappings.split(';').forEach((entry: string, genLine: number) => {
        let genColumn = 0;
        for (const field of entry ? entry.split(',') : []) {
            if (!field) continue;
            const f = decodeVLQ(field);
            genColumn += f[0];
            if (f.length >= 4) {
                source += f[1];
                line += f[2];
                column += f[3];
            }
            if (lineStart[genLine] === undefined) continue;
            segments.push({ at: lineStart[genLine] + genColumn, source: f.length >= 4 ? source : -1, line });
        }
    });
}
segments.sort((a, b) => a.at - b.at);

// Group every output byte under whatever key the caller names it by.
const owned = (keyOf: (segment: Segment) => string | null) => {
    const ranges = new Map<string, [number, number][]>();
    const chars = new Map<string, number>();
    for (let i = 0; i < segments.length; i++) {
        const start = segments[i].at;
        const end = i + 1 < segments.length ? segments[i + 1].at : js.length;
        if (end <= start) continue;
        const key = keyOf(segments[i]);
        if (key === null) continue;
        if (!ranges.has(key)) ranges.set(key, []);
        ranges.get(key)!.push([start, end]);
        chars.set(key, (chars.get(key) ?? 0) + (end - start));
    }
    return { ranges, chars };
};

const priceEach = async (ranges: Map<string, [number, number][]>, chars: Map<string, number>): Promise<void> => {
    for (const [name, count] of [...chars].sort((a, b) => b[1] - a[1])) {
        const range = ranges.get(name);
        if (range) rows.push([name, count, base - (await zipSize(cut(range)))]);
    }
};

if (wantFloor) {
    // What the package costs before a single byte of game.
    const shell = (await zipOf(inline(baseHtml, ''))).length;
    console.log(`\n  html shell, no script at all      ${String(shell).padStart(6)}`);
    console.log(`  + Roadroller decoder (1-char js)  ${String(await zipSize('0')).padStart(6)}`);
    console.log(`  the full package                  ${String(base).padStart(6)}\n`);
} else if (wantModel) {
    // The compact model is ONE template literal in the bundle, so it is priced
    // by re-encoding rather than by byte range: parse it, blank one field,
    // splice the JSON back in, pack.
    const start = js.indexOf('`pc2![[') + 1;
    const end = js.indexOf('`', start);
    const payload = js.slice(start, end);
    const model = JSON.parse(payload.slice(4));
    if ('pc2!' + JSON.stringify(model) !== payload) {
        console.error('the compact payload did not re-serialize identically; picocad_compact.ts changed shape');
        process.exit(1);
    }
    const clone = () => JSON.parse(JSON.stringify(model));
    const swap = (m: unknown) => js.slice(0, start) + 'pc2!' + JSON.stringify(m) + js.slice(end);
    const price = async (name: string, m: unknown, chars: number) =>
        rows.push([name, chars, base - (await zipSize(swap(m)))]);
    // Node tuple: [visible, pos, rot, scale, mesh, children, name?]
    const eachMesh = (m: any, fn: (node: any) => void): void => {
        const walk = (node: any) => {
            if (node[4]) fn(node);
            (node[5] ?? []).forEach(walk);
        };
        walk(m[1]);
    };

    let m = clone();
    let n = m[0][0].length;
    m[0][0] = '';
    await price('texture pixels', m, n);

    m = clone();
    n = m[0][1].length;
    m[0][1] = '';
    await price('palette (hex)', m, n);

    m = clone();
    n = JSON.stringify([m[0][3], m[0][4]]).length;
    m[0][3] = [];
    m[0][4] = [];
    await price('shade tables', m, n);

    m = clone();
    n = JSON.stringify(m[1]).length;
    m[1] = 0;
    await price('ALL geometry', m, n);

    m = clone(); n = 0;
    eachMesh(m, (o) => { n += JSON.stringify(o[4][0]).length; o[4][0] = []; });
    await price('  vertex positions', m, n);

    m = clone(); n = 0;
    eachMesh(m, (o) => o[4][1].forEach((f: any) => { n += JSON.stringify(f[0]).length; f[0] = 0; }));
    await price('  face vertex indices', m, n);

    m = clone(); n = 0;
    eachMesh(m, (o) => o[4][1].forEach((f: any) => { n += JSON.stringify(f[1]).length; f[1] = 0; }));
    await price('  face UVs', m, n);

    m = clone(); n = 0;
    eachMesh(m, (o) => o[4][1].forEach((f: any) => { n += JSON.stringify([f[2], f[3]]).length; f[2] = 0; f[3] = 0; }));
    await price('  face colour + flags', m, n);

    m = clone(); n = 0;
    eachMesh(m, (o) => { if (o[6]) { n += o[6].length + 3; o[6] = ''; } });
    await price('  mesh names', m, n);

    const names: string[] = [];
    eachMesh(model, (o) => names.push(o[6] ?? '(unnamed)'));
    for (let i = 0; i < names.length; i++) {
        const variant = clone();
        const list: any[] = [];
        eachMesh(variant, (o) => list.push(o));
        const chars = JSON.stringify(list[i][4]).length;
        const verts = list[i][4][0].length / 3;
        const faces = list[i][4][1].length;
        list[i][4] = 0;
        rows.push([`  ${names[i]} (${verts}v ${faces}f)`, chars, base - (await zipSize(swap(variant)))]);
    }
    report('inside the compact model', 'chars', false);
} else if (target) {
    const source = readFileSync(target, 'utf8').split('\n');
    // Section banners if the file has them, one region per export if not.
    // A banner is dashes on BOTH sides — `// --- name ---`. Two dashes and no
    // trailing run is how this file writes a continuation line inside an
    // ordinary comment, and matching one of those invents a section.
    const marks: [string, number][] = [];
    source.forEach((line, i) => {
        const banner = /^\/\/ -{3,} *(.+?) *-{3,}$/.exec(line);
        if (banner) marks.push([banner[1], i + 1]);
    });
    if (!marks.length) {
        source.forEach((line, i) => {
            const exported = /^export (?:const|function|let) ([A-Za-z_$][\w$]*)/.exec(line);
            if (exported) marks.push([exported[1], i + 1]);
        });
    }
    if (!marks.length) {
        console.error(`no // --- banners --- and no exports found in ${target}`);
        process.exit(1);
    }
    if (marks[0][1] > 1) marks.unshift(['(head of file)', 1]);
    // A region runs from its banner to the next one, so the LINE RANGE is part
    // of the name: a file whose banners stop half way through has one giant
    // trailing region, and seeing `(393-743)` next to it says so immediately.
    // The fix for a coarse row is a banner in the source, not a flag here.
    const regions: [string, number, number][] = marks.map(([name, at], i) => {
        const to = i + 1 < marks.length ? marks[i + 1][1] - 1 : source.length;
        return [`${name} (${at}-${to})`, at, to];
    });

    const file = label(target);
    const { ranges, chars } = owned((s) =>
        s.source >= 0 && sources[s.source].split('?')[0] === file
            ? regions.find(([, from, to]) => s.line + 1 >= from && s.line + 1 <= to)?.[0] ?? '(outside every region)'
            : null,
    );
    if (!ranges.size) {
        console.error(`${file} contributed no bytes to the bundle (tree-shaken, or not the path the map uses)`);
        process.exit(1);
    }
    await priceEach(ranges, chars);
    report(file);
} else {
    const { ranges, chars } = owned((s) => (s.source < 0 ? '(bundle wrapper)' : sources[s.source]));
    await priceEach(ranges, chars);
    report('by module');
}

console.log(`  (${packs} packs)\n`);
if (existsSync(OUT)) rmSync(OUT, { recursive: true, force: true });
