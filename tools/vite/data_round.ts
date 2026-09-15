import type { Plugin } from 'vite';

// Build-only rounding of the Blender-exported numbers in entities.js and
// stages.js. The addon writes four decimals (14.8335, 0.7785), which is more
// than a 600x800 chunky-pixel render can show, and every digit is novel data
// Roadroller pays full price for. The authored files stay at full precision;
// only the pack rounds. Dev serves the raw files (apply: 'build').
//
// Set DIGITS from a measurement, not a hunch: run `bun run pack` and the
// runtime verifier against a saved package at each candidate and keep the
// smallest that draws acceptably. Rounding pos/rot/scale of a part is
// amplified by every scale above it in the parent chain, so a placement's
// error and a part's error are not the same size on screen.

const FILES = /[\\/]src[\\/](entities|stages)\.js$/;
const NUMBER = /-?\d+\.\d+/g;

export function dataRound(digits: number): Plugin {
    const p = 10 ** digits;
    return {
        name: 'data-round',
        apply: 'build',
        transform(code, id) {
            if (!FILES.test(id)) return null;
            return {
                code: code.replace(NUMBER, n => String(Math.round(Number(n) * p) / p)),
                map: null,
            };
        },
    };
}
