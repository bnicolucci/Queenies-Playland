// Runs the whole Blender-addon test suite: `bun run test:blender`.
//
// Each Blender test drives the addon headlessly and writes its blueprint plus
// the engine-space matrices it expects into a scratch dir; the browser checks
// then run those blueprints through the REAL src/entity.js in headless Chrome.
// Nothing writes into the repo -- src/entities.js is only ever read.
//
// Needs Blender (set BLENDER to override discovery) with the "PicoCad2 Blender
// Tools" addon installed, and Chrome for the browser checks.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const HERE = import.meta.dir;
const REPO = resolve(HERE, '..', '..', '..');

function findBlender(): string {
  if (process.env.BLENDER) return process.env.BLENDER;
  const roots = ['C:/Program Files/Blender Foundation', 'C:/Program Files (x86)/Blender Foundation'];
  const found: string[] = [];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const dir of readdirSync(root)) {
      const exe = join(root, dir, 'blender.exe');
      if (existsSync(exe)) found.push(exe);
    }
  }
  // newest install wins; "Blender 5.2" sorts after "Blender 4.5"
  found.sort();
  if (found.length) return found[found.length - 1];
  for (const p of ['/usr/bin/blender', '/Applications/Blender.app/Contents/MacOS/Blender']) {
    if (existsSync(p)) return p;
  }
  throw new Error('Blender not found -- set BLENDER=/path/to/blender');
}

const BLENDER = findBlender();
const OUT = mkdtempSync(join(tmpdir(), 'pc2-tests-'));
const env = { ...process.env, PC2_TEST_OUT: OUT };

const BLENDER_TESTS = ['smoke_entity', 'smoke_stage', 'smoke_view', 'smoke_roundtrip', 'smoke_shear', 'smoke_noscale', 'smoke_uv', 'smoke_mirror', 'smoke_pivot', 'smoke_compound', 'smoke_states', 'smoke_game_preview', 'smoke_subentities'];
// [blueprint, expected, entity] -- each produced by the Blender test above it
const BROWSER_CHECKS: [string, string, string, string?][] = [
  ['entities_subentities.js', 'expected_subentities.json', 'FACE'],
  ['entities_test.js', 'expected.json', 'TESTBOT'],
  ['entities_shear.js', 'expected_shear.json', 'SHEARTEST'],
  ['entities_noscale.js', 'expected_noscale.json', 'NOSCALE'],
  ['entities_pivot.js', 'expected_pivot.json', 'PIVOTTEST'],
  ['entities_compound.js', 'expected_compound.json', 'COMPOUNDTEST'],
  ['entities_states.js', 'expected_states.json', 'UNICORN', '2'],
];

const verbose = process.argv.includes('--verbose');
console.log(`blender: ${BLENDER}\nscratch: ${OUT}\n`);

const results: [string, boolean][] = [];

for (const name of BLENDER_TESTS) {
  const res = spawnSync(BLENDER, [
    '--background', '--factory-startup', '--python', join(HERE, `${name}.py`),
  ], { encoding: 'utf8', env });
  const output = (res.stdout ?? '') + (res.stderr ?? '');
  // Blender exits 0 even when the script died on a traceback, so the exit code
  // alone reports a crashed test as a pass. _harness.finish() is the only thing
  // that prints this marker, and only after every check has run.
  const ok = res.status === 0 && output.includes('ALL PASS');
  results.push([name, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok || verbose) {
    if (res.status === 0 && !/ALL PASS|FAILURES/.test(output)) {
      console.log('      the script never reached the end -- traceback below');
    }
    for (const line of output.split('\n')) {
      if (verbose || /FAIL|Error|Traceback|FAILURES/.test(line)) console.log('      ' + line.trimEnd());
    }
  }
}

for (const [bp, exp, entity, state] of BROWSER_CHECKS) {
  const res = spawnSync('bun', [join(HERE, 'verify_engine.ts'), bp, exp, entity,
                                ...(state === undefined ? [] : [state])],
                        { encoding: 'utf8', env, cwd: REPO });
  const output = (res.stdout ?? '') + (res.stderr ?? '');
  const ok = res.status === 0;
  results.push([`browser:${entity}`, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  browser:${entity}`);
  if (!ok || verbose) {
    for (const line of output.split('\n')) {
      if (verbose || /FAIL|Error|FAILURES/.test(line)) console.log('      ' + line.trimEnd());
    }
  }
}

// The camera check has its own script: its fixture is a camera.json written by
// smoke_stage, not a blueprint, so it does not fit BROWSER_CHECKS' shape.
for (const fixture of ['camera.json', 'camera-portrait.json']) {
  const res = spawnSync('bun', [join(HERE, 'verify_camera.ts'), fixture],
                        { encoding: 'utf8', env, cwd: REPO });
  const output = (res.stdout ?? '') + (res.stderr ?? '');
  const ok = res.status === 0;
  results.push([`browser:${fixture}`, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  browser:${fixture}`);
  if (!ok || verbose) {
    for (const line of output.split('\n')) {
      if (verbose || /FAIL|Error|FAILURES/.test(line)) console.log('      ' + line.trimEnd());
    }
  }
}

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) {
  console.log(`failed: ${failed.map(([n]) => n).join(', ')}`);
  console.log('re-run with --verbose for full output');
}
process.exit(failed.length ? 1 : 0);
