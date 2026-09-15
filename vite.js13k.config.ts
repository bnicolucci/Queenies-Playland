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
// tools/vite/data_round.ts. Measured on the 12-frame runtime verifier against
// the full-precision package (pixels differing per 405x540 frame):
//   3 decimals  -246 zip bytes   60..650 px    sub-pixel shimmer, invisible
//   2 decimals  -572 zip bytes   ~2,700 px     still indistinguishable by eye
//   1 decimal   -928 zip bytes   ~18-24k px    the prize wheel visibly turns,
//                                              hub face breaks -- too coarse
const DATA_DIGITS = 2;

export default defineConfig({
    plugins: [glslMin(), picocadCompact(), dataRound(DATA_DIGITS)],
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
