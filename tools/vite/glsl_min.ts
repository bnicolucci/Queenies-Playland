import { readFile } from 'node:fs/promises';
import type { Plugin } from 'vite';

// Build-only GLSL shrink for `*.vert?raw` / `*.frag?raw` imports: shader
// source ships inside a JS string, which no JS minifier touches — so comments
// and indentation in the .vert/.frag files would go into the zip verbatim.
// This strips comments, collapses whitespace around punctuation, and joins
// lines. Dev serves the readable files untouched (apply: 'build').
// Keep GLSL comments as documentation — they're free.

export function glslMin(): Plugin {
    return {
        name: 'glsl-min',
        apply: 'build',
        enforce: 'pre',
        async load(id) {
            const m = id.match(/^(.*\.(?:vert|frag))\?raw$/);
            if (!m) return null;
            const min = (await readFile(m[1], 'utf8'))
                .replace(/\/\/[^\n]*/g, '')
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .replace(/\s*\n\s*/g, '\n')
                .replace(/\n+/g, '\n')
                // Collapsing whitespace around punctuation also eats most
                // newlines; the ones between word-ending and word-starting
                // lines (e.g. after `#version 300 es`) survive, as they must.
                .replace(/\s*([{}()+\-*/=,;<>!?:])\s*/g, '$1')
                // Shorter equivalent floating-point literals. Keep this in
                // the build transform so the readable shaders stay readable.
                .replace(/\b0\.(\d+)/g, '.$1')
                .replace(/\b(\d+)\.0\b/g, '$1.')
                .trim();
            return `export default ${JSON.stringify(min)};`;
        },
    };
}
