// Verify a Blender-exported blueprint inside a real browser, against the REAL
// src/entity.js spawnEntity. The Blender tests write the blueprint and the
// engine-space matrices they expect; this runs spawnEntity on that blueprint in
// headless Chrome and diffs the two.
//
// It also settles the one assumption the Blender side cannot check for itself:
// that DOMMatrix.rotate(rx, ry, rz) composes Rz*Ry*Rx, which is what makes
// Blender's 'XYZ' euler order the right one to export.
//
// Drives CDP over bun's native WebSocket -- playwright-core hangs under bun on
// Windows (see CLAUDE.md).
import { spawn } from 'node:child_process';
import { readFileSync, mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const HERE = import.meta.dir;
const REPO = resolve(HERE, '..', '..', '..');
const OUT = process.env.PC2_TEST_OUT || join(HERE, '.out');

// argv: [blueprint file] [expected json] [entity name] [optional state index]
const [bpFile = 'entities_test.js', expFile = 'expected.json', ENTITY = 'TESTBOT', stateArg] =
  process.argv.slice(2);
const stateIndex = stateArg === undefined ? null : Number(stateArg);

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(p => existsSync(p));
if (!CHROME) throw new Error('Chrome not found');

const port = 9333 + (process.pid % 200);
const profile = mkdtempSync(join(tmpdir(), 'pc2-cdp-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank',
], { stdio: 'ignore' });

const wsUrl = await (async () => {
  for (let i = 0; i < 100; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t: any) => t.type === 'page');
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('CDP never came up');
})();

const ws = new WebSocket(wsUrl);
await new Promise(r => (ws.onopen = r));
let nextId = 1;
const pending = new Map<number, (v: any) => void>();
ws.onmessage = e => {
  const msg = JSON.parse(e.data as string);
  if (msg.id && pending.has(msg.id)) pending.get(msg.id)!(msg);
};
const send = (method: string, params: any = {}) =>
  new Promise<any>(res => {
    const id = nextId++;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });

const evaluate = async (expression: string) => {
  const msg = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (msg.error) throw new Error('CDP: ' + JSON.stringify(msg.error));
  const { result, exceptionDetails } = msg.result ?? {};
  if (exceptionDetails) throw new Error('page: ' + JSON.stringify(exceptionDetails.exception ?? exceptionDetails));
  return result?.value;
};

// Injected into Runtime.evaluate as a CLASSIC script, where `import.meta` is a
// syntax error -- so entity.js's DEV-only guards get folded to `true`, exactly
// as vite folds them for `bun run dev`. Without this the whole browser half of
// the suite dies on a SyntaxError the moment entity.js grows a dev guard.
const entityJs = ['anim.js', 'entity.js'].map(file =>
  readFileSync(join(REPO, 'src', file), 'utf8')
    .replace(/^export /gm, '')
    .replace(/^import .* from '\.\/anim\.js';\r?$/gm, '')
    .replace(/import\.meta\.env\.DEV/g, 'true')).join('\n');
const blueprintJs = readFileSync(join(OUT, bpFile), 'utf8').replace(/^export /gm, '');
const expected = JSON.parse(readFileSync(join(OUT, expFile), 'utf8'));

let failures = 0;
const check = (label: string, ok: boolean, detail = '') => {
  console.log((ok ? '  OK   ' : '  FAIL ') + label + (detail ? '   ' + detail : ''));
  if (!ok) failures++;
};

console.log(`\nchecking ${ENTITY}${stateIndex === null ? '' : ` state ${stateIndex}`} from ${bpFile}`);
console.log('\n[1] DOMMatrix.rotate composition order');
// Single-axis rotations are unambiguous, so composing them settles which order
// the three-argument form uses.
const orders = await evaluate(`(() => {
  const R = (x, y, z) => new DOMMatrix().rotate(x, y, z);
  const target = R(37, 11, 53).toFloat32Array();
  const rx = R(37, 0, 0), ry = R(0, 11, 0), rz = R(0, 0, 53);
  const eq = m => Array.from(m.toFloat32Array()).every((v, i) => Math.abs(v - target[i]) < 1e-5);
  return { 'Rz*Ry*Rx': eq(rz.multiply(ry).multiply(rx)),
           'Rx*Ry*Rz': eq(rx.multiply(ry).multiply(rz)) };
})()`);
check("rotate(rx,ry,rz) === Rz*Ry*Rx (Blender's 'XYZ')", orders['Rz*Ry*Rx'] === true,
      JSON.stringify(orders));

console.log('\n[2] real spawnEntity vs Blender world matrices');
const got = await evaluate(`(() => {
  ${entityJs}
  ${blueprintJs}
  // A pivot has no mesh, and meshByName[undefined] looks up the STRING
  // "undefined" -- which a bare proxy would answer truthily, hiding the
  // one thing that makes a pivot a pivot.
  const meshes = new Proxy({}, { get: (_, k) => (k === 'undefined' ? undefined : k) });
  const parts = spawnEntity(${ENTITY}, meshes);
  const state = ${JSON.stringify(stateIndex)};
  if (state !== null) poseState(parts, ${ENTITY}, ${ENTITY}.s[state]);
  let tweenMatrices = null;
  if (state !== null && ${ENTITY}.s.length > 1) {
    const tweenParts = spawnEntity(${ENTITY}, meshes);
    poseState(tweenParts, ${ENTITY}, ${ENTITY}.s[0], ${ENTITY}.s[1], .5);
    tweenMatrices = tweenParts.map(p => Array.from(p.local.toFloat32Array()));
  }
  return {
    matrices: parts.map(p => Array.from(p.local.toFloat32Array())),
    mask: state === null ? -1 : ${ENTITY}.s[state][0],
    tweenMatrices,
  };
})()`);
check('part count', got.matrices.length === expected.matrices.length,
      `${got.matrices.length} vs ${expected.matrices.length}`);
for (let i = 0; i < expected.matrices.length; i++) {
  const want = expected.matrices[i];
  const worst = Math.max(...want.map((v: number, j: number) => Math.abs(v - got.matrices[i][j])));
  check(`${expected.names[i]} local matrix`, worst < 1e-4, `max delta ${worst.toExponential(1)}`);
}
if (expected.visible) {
  expected.visible.forEach((visible: boolean, i: number) =>
    check(`${expected.names[i]} visibility`, Boolean(got.mask >> i & 1) === visible));
}
if (expected.tween_matrices) {
  for (let i = 0; i < expected.tween_matrices.length; i++) {
    const want = expected.tween_matrices[i];
    const worst = Math.max(...want.map((v: number, j: number) =>
      Math.abs(v - got.tweenMatrices[i][j])));
    check(`${expected.names[i]} tween matrix`, worst < 1e-4,
          `max delta ${worst.toExponential(1)}`);
  }
}

console.log(failures ? `\nFAILURES: ${failures}` : '\nALL PASS');
ws.close();
chrome.kill();
process.exit(failures ? 1 : 0);
