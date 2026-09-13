// The js13k package, end to end:
//
//   bun run pack
//   bun run pack:tune     # occasionally, after a substantial bundle change
//
//   vite build -> Roadroller -> inline into one HTML -> zip -> report
//
// js13k measures the ZIP, so that is the number this prints and compares
// against the 13,312 byte limit. Everything ends up in a single index.html, so
// the zip holds exactly one file.
import { existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { DIRTY, TUNED, inline as inlineInto, run, zipOf } from './pipeline';

const LIMIT = 13312;
const OUT = 'dist13k';

const options = process.argv.slice(2);
const unknown = options.filter(option => option !== '--tune');
if (unknown.length) {
    console.error(`Unknown pack option: ${unknown.join(' ')}`);
    console.error('Use `bun run pack` or `bun run pack:tune`.');
    process.exit(1);
}

// --- 1. build ---------------------------------------------------------------
run('bunx', ['vite', 'build', '--config', 'vite.js13k.config.ts']);

const entry = `${OUT}/app.js`;
if (!existsSync(entry)) {
    console.error(`expected ${entry}; the build config should emit a single IIFE entry`);
    process.exit(1);
}
const js = readFileSync(entry, 'utf8');
const baseHtml = readFileSync(`${OUT}/index.html`, 'utf8');

// --- 2. Roadroller ----------------------------------------------------------
// Its output is a self-extracting classic script, which is why the build emits
// IIFE rather than ESM.
//
// Roadroller's pinned parameters and the HTML/zip steps live in pipeline.ts,
// because breakdown.ts runs the identical steps to price a piece of the bundle
// by cutting it out and re-packing. Its numbers only mean anything while both
// programs use the same flags.
const tune = process.argv.includes('--tune');
const roll = (args: string[], out: string, quiet = false): { script: string; log: string } => {
    const log = run('bunx', ['roadroller', entry, ...DIRTY, ...args, '-o', out], quiet);
    return { script: readFileSync(out, 'utf8'), log };
};
if (tune) console.log('\n  tuning Roadroller (~45 seconds; rejected candidates hidden)...');
const searched = roll(tune ? ['-O2'] : ['-O0', ...TUNED], `${OUT}/app.rr.js`, tune);
const rolled = searched.script;
const searchedFlags = tune
    ? searched.log.match(/use `([^`]+)` to replicate/)?.[1].trim().split(/\s+/)
    : undefined;

// --- 3. inline into one HTML ------------------------------------------------
const inline = (script: string): string => inlineInto(baseHtml, script);

// --- 4. zip -----------------------------------------------------------------
const html = inline(rolled);
const zip = await zipOf(html);

// --tune only: pack the pinned parameters too, so the report compares the
// number that decides this (the ZIP) rather than the search's own estimate.
const pinned = tune
    ? await (async () => {
          const script = roll(['-O0', ...TUNED], `${OUT}/app.pinned.rr.js`, true).script;
          const page = inline(script);
          return { rolled: script, html: page, zip: await zipOf(page) };
      })()
    : null;

// dist13k always holds the SMALLER of the two, so a losing --tune can't leave a
// worse package behind for someone to ship by accident.
const ship = pinned && pinned.zip.length < zip.length ? pinned : { rolled, html, zip };
writeFileSync(`${OUT}/index.html`, ship.html, 'utf8');
writeFileSync(`${OUT}/game.zip`, ship.zip);

// Only index.html ships; the loose build artefacts would just confuse.
for (const file of readdirSync(OUT)) {
    if (file !== 'index.html' && file !== 'game.zip') rmSync(`${OUT}/${file}`, { recursive: true });
}

// --- 5. report --------------------------------------------------------------
const pct = (n: number): string => `${((n / LIMIT) * 100).toFixed(1)}%`;
const bar = (n: number): string => {
    const width = 34;
    const filled = Math.min(width, Math.round((n / LIMIT) * width));
    return `[${'#'.repeat(filled)}${'.'.repeat(width - filled)}]`;
};

console.log('');
console.log(`  bundle (raw js)   ${String(js.length).padStart(7)}`);
console.log(`  roadrolled        ${String(ship.rolled.length).padStart(7)}   ${tune ? 'the better of the two below' : 'pinned params (pack:tune to re-search)'}`);
console.log(`  single html       ${String(ship.html.length).padStart(7)}`);
console.log('  ------------------------------');
console.log(`  ZIP               ${String(ship.zip.length).padStart(7)}   ${pct(ship.zip.length)} of 13312`);
console.log(`  ${bar(ship.zip.length)}`);
console.log(
    ship.zip.length <= LIMIT
        ? `  ${LIMIT - ship.zip.length} bytes to spare -> ${OUT}/game.zip`
        : `  OVER by ${ship.zip.length - LIMIT} bytes -> ${OUT}/game.zip`,
);
if (pinned) {
    const delta = pinned.zip.length - zip.length;
    console.log('');
    console.log(`  searched ZIP      ${String(zip.length).padStart(7)}   fresh Roadroller search`);
    console.log(`  pinned ZIP        ${String(pinned.zip.length).padStart(7)}   the TUNED flags already in pack.ts`);
    if (delta > 0) {
        console.log(`  the searched flags WIN by ${delta}`);
        if (searchedFlags) {
            console.log('\n  Replace TUNED in tools/js13k/pack.ts with:');
            console.log(`\nconst TUNED = [${searchedFlags.map(flag => `'${flag}'`).join(', ')}];`);
            console.log('\n  Then run: bun run pack');
        } else {
            console.log('  Roadroller did not print parseable flags; leave TUNED unchanged.');
        }
    } else {
        console.log(`  the searched flags lose by ${-delta} — no edit needed; the pin stands`);
    }
}
console.log('');
