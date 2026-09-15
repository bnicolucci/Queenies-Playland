import { defineConfig } from 'vite';
import { glslMin } from './tools/vite/glsl_min.ts';
import { picocadCompact } from './tools/vite/picocad_compact.ts';
import { dataRound } from './tools/vite/data_round.ts';

// Build config for the js13k package (`bun run pack`). It differs from the
// normal build in ways that only matter when everything ends up inside one
// HTML file:
//
//  - IIFE, not ESM: Roadroller's output is a self-extracting classic script,
//    so the bundle must run without module semantics.
//  - One chunk: a dynamic import would otherwise become a second file that the
//    inlined HTML could not fetch.
//  - No hashed names: pack.ts looks the entry up by a fixed name.
//
// PROPERTY NAMES THE PACK RENAMES. esbuild/Terser rename every local to one
// letter but leave property names alone, because a property might belong to
// the browser (gl.bindBuffer, matrix.scale, canvas.width). This is the
// explicit list of names that are OURS on every object they appear on, so
// renaming them everywhere is safe. Blueprint keys dominate: `color:` alone
// appears ~150 times in the bundle.
//
// A name goes on this list ONLY if no native object is ever read or called
// through it anywhere in src/ -- Terser renames every occurrence, and a miss
// breaks the game at runtime with no build error. Deliberately absent, and
// why:  scale/translate/rotate (DOMMatrix methods), min/max (Math AND mesh
// bounds), width/height (canvas), value/start/stop/loop/duration (WebAudio),
// target (event.target, and TAGS is keyed by the tag STRINGS), name/data
// (Object.fromEntries keys / typed arrays -- tiny win, not worth the doubt).
// Quoted strings are never touched, so 'mesh_cube' style lookups and the
// shader source are safe regardless.
const MANGLE_PROPS = new RegExp('^(' + [
    // blueprint parts (entities.js) and spawned parts (entity.js)
    'mesh', 'pos', 'rot', 'color', 'parent', 'uv', 'local', 'tile', 'rect',
    // engine mesh records and instancing
    'inst', 'vao', 'ivbo', 'count', 'palette', 'pixels', 'texture', 'shades', 'objects',
    'draw', 'flush', 'setPalette', 'setModel',
    // camera
    'at', 'yaw', 'pitch', 'dist', 'fov', 'shake', 'jx', 'jy', 'update', 'view',
    // game objects (main.js)
    'place', 'parts', 'bounce',
    // picoCAD parse tree (pico.js decoder builds these; only dev reads the JSON)
    'graph', 'children', 'visible', 'transform', 'vertices', 'faces', 'vertex_ids',
    'uvs', 'noshade', 'notex', 'colors', 'shade_pal_1', 'shade_pal_2',
    'transparent_color', 'background_color', 'transparentColor', 'bg',
].join('|') + ')$');

// DECIMALS KEPT in the Blender-exported numbers (entities.js, stages.js), see
// tools/vite/data_round.ts: DATA_DIGITS for ordinary values, DATA_FINE for
// values under 1 and for exact quarters (1.25, 0.75). Measured on the 12-frame
// runtime verifier (pixels differing per 405x540 frame; ~3k is sub-pixel
// shimmer, the level the 2-decimal build itself sits at against 4 decimals):
//   4 decimals everywhere                    (reference)
//   3 everywhere        -246 zip bytes    60..650 px     invisible
//   2 everywhere        -572              ~2,700 px      indistinguishable
//   1 everywhere        -930              ~18-24k px     eyes sink into heads,
//                                                        wheel rises 6 px
//   1, small values 2   -703              wheel still rises: 1.25 -> 1.3
//   1, small+quarters 2 -679              ~2,500-3,600 px  <- pinned
const DATA_DIGITS = 1, DATA_FINE = 2;

export default defineConfig({
    plugins: [glslMin(), picocadCompact(), dataRound(DATA_DIGITS, DATA_FINE)],
    build: {
        outDir: 'dist13k',
        emptyOutDir: true,
        target: 'es2022',
        cssCodeSplit: false,
        assetsInlineLimit: Number.MAX_SAFE_INTEGER,
        modulePreload: { polyfill: false },
        reportCompressedSize: false,
        minify: 'terser',
        terserOptions: {
            ecma: 2020,
            compress: { passes: 3, unsafe_arrows: true },
            mangle: {
                toplevel: true,
                properties: {
                    builtins: true,
                    regex: MANGLE_PROPS,
                },
            },
        },
        rollupOptions: {
            output: {
                format: 'iife',
                entryFileNames: 'app.js',
                assetFileNames: '[name][extname]',
            },
        },
    },
});
