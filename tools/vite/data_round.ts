import type { Plugin } from 'vite';

// Build-only rounding of the Blender-exported numbers in entities.js and
// stages.js. The addon writes four decimals (14.8335, 0.7785), which is more
// than a 600x800 chunky-pixel render can show, and every digit is novel data
// Roadroller pays full price for. The authored files stay at full precision;
// only the pack rounds. Dev serves the raw files (apply: 'build').
//
// Two precisions, chosen by measurement (see the table in vite.js13k.config.ts):
//
//   - `digits` for ordinary values (a rotation of 14.8335, a position of 5.2059).
//   - `fineDigits` for two kinds of value that `digits` would visibly damage:
//       values under 1, which are mostly small offsets sitting under a large
//       parent scale (an eye at -0.1647 rounded to -0.2 moved into the head), and
//       exact quarters like 1.25, which are hand-authored, exact at two decimals,
//       and wrong at one (the prize wheel's pivot rounded 1.25 -> 1.3 and the
//       whole wheel rose six pixels).
//
// Rounding pos/rot/scale of a part is amplified by every scale above it in the
// parent chain, so a placement's error and a part's error are not the same size
// on screen; re-measure with the runtime verifier before changing either digit.

const FILES = /[\/]src[\/](entities|stages)\.js$/;
const NUMBER = /-?\d+\.\d+/g;

export function dataRound(digits: number, fineDigits: number): Plugin {
    const p = 10 ** digits, pf = 10 ** fineDigits;
    return {
        name: 'data-round',
        apply: 'build',
        transform(code, id) {
            if (!FILES.test(id)) return null;
            return {
                code: code.replace(NUMBER, n => {
                    const v = Number(n);
                    const q = Math.abs(v) < 1 || Math.round(v * 100) % 5 === 0 ? pf : p;
                    return String(Math.round(v * q) / q);
                }),
                map: null,
            };
        },
    };
}
