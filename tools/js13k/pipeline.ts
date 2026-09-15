// The half of the packer that is not the packer: the Roadroller parameters and
// the "script -> single HTML -> zip" steps, in one place because TWO programs
// run them. pack.ts ships the game; breakdown.ts prices pieces of it by cutting
// them out and re-packing, and its numbers are only comparable to the shipped
// ZIP while both go through exactly these steps with exactly these flags. A
// second copy of the pinned parameters would be a second thing to re-tune.
import { spawnSync } from 'node:child_process';
import { makeZip } from './zip';

// PINNED PARAMETERS, on purpose. Roadroller's default optimizer is a TIME-BOXED
// search, so it explores more or fewer configurations depending on what else
// the machine is doing: packing identical code twice here gave 11,508 and
// 11,423. An 85-byte swing is bigger than most size experiments worth running,
// so an unpinned pack cannot tell you whether a change helped. `-O0` plus the
// parameters the search found makes every pack byte-identical for the same
// input, and comparable across runs.
//
// Re-tune after a big change:  bun run pack:tune
//
// Read the search's own number with care: it is Roadroller's estimate of ITS
// OWN output stream, not the ZIP, and the two disagree often enough to matter
// — the first search run after the black-background palette change printed a
// smaller estimate than the pinned params and zipped 9 bytes BIGGER. Tuning
// therefore hides the rejected candidates, packs the searched flags AND the
// pinned ones, then prints an exact TUNED replacement only if the search wins.
//
// The search is -O2 (~300 attempts, ~45s), not -O1 (~30 attempts, ~5s): -O1
// lands somewhere different every run — four runs here spanned 8,520..8,567
// real zip bytes around a pinned 8,548 — while all three -O2 runs beat all four
// -O1 runs. 45 seconds is nothing next to pinning a worse number for months.
export const TUNED = ['-Zab31', '-Zlr951', '-Zmc4', '-Zmd29', '-Zpr14', '-S0,1,2,3,7,13,21,26,205,348,418,425'];

// Not a tuned parameter, and not part of the search: -D lets Roadroller's
// decoder keep its state in single-letter globals instead of scoping them,
// worth 23 zip bytes. It is safe ONLY while index.html has no script of its own
// and no single-letter element id (today: one <canvas>, one #hint). Adding
// either means dropping this flag — verified by packing and loading the page.
export const DIRTY = ['-D'];

export const run = (command: string, args: string[], quiet = false): string => {
    const result = spawnSync(command, args, {
        encoding: 'utf8',
        stdio: quiet ? 'pipe' : 'inherit',
        shell: process.platform === 'win32',
    });
    if (result.status !== 0) {
        if (quiet) {
            process.stdout.write(result.stdout ?? '');
            process.stderr.write(result.stderr ?? '');
        }
        console.error(`\n${command} ${args.join(' ')} failed`);
        process.exit(1);
    }
    return quiet ? (result.stdout ?? '') + (result.stderr ?? '') : '';
};

// The kept blocks are the page's own small scripts/styles, so a conservative
// minify is safe: drop full-line // comments and CSS /* */ comments, then
// collapse indentation and blank lines. Style blocks additionally lose all
// newlines and the spaces around punctuation — safe for CSS, not for JS.
const minifyBlock = (block: string): string => {
    const collapsed = block
        .replace(/^\s*\/\/[^\n]*$/gm, '')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\s*\n\s*/g, '\n')
        .replace(/\n+/g, '\n');
    if (!/^<style/.test(collapsed)) return collapsed;
    return collapsed.replace(/\n/g, '').replace(/\s*([{}:;,])\s*/g, '$1').replace(/;}/g, '}');
};

// Drop every <script src>, then minify the markup around the inline blocks.
export const inline = (baseHtml: string, script: string): string => {
    let html = baseHtml.replace(/<script\b[^>]*\bsrc=[^>]*><\/script>/g, '');
    // Placeholders use \0 (impossible in HTML source), so body text like
    // "press 1 to start" can never collide with them.
    const keep: string[] = [];
    html = html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/g, (block) => `\0${keep.push(minifyBlock(block)) - 1}\0`);
    html = html
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/\s*\n\s*/g, '\n')
        .replace(/>\s+</g, '><')
        .trim();
    html = html.replace(/\0(\d+)\0/g, (_, i: string) => keep[Number(i)]);
    return html.replace('</body>', `<script>${script}</script></body>`);
};

export const zipOf = (page: string): Promise<Uint8Array> =>
    makeZip([{ name: 'index.html', data: new TextEncoder().encode(page) }]);
