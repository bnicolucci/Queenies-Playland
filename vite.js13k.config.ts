import { defineConfig } from 'vite';
import { glslMin } from './tools/vite/glsl_min.ts';
import { picocadCompact } from './tools/vite/picocad_compact.ts';

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
export default defineConfig({
    plugins: [glslMin(), picocadCompact()],
    build: {
        outDir: 'dist13k',
        emptyOutDir: true,
        target: 'es2022',
        cssCodeSplit: false,
        assetsInlineLimit: Number.MAX_SAFE_INTEGER,
        modulePreload: { polyfill: false },
        reportCompressedSize: false,
        rollupOptions: {
            output: {
                format: 'iife',
                entryFileNames: 'app.js',
                assetFileNames: '[name][extname]',
            },
        },
    },
});
