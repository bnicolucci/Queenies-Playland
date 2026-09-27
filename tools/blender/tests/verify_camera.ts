// Verify the addon's game camera against the REAL src/engine.js, in a browser.
//
// The addon rebuilds engine.js's orbitView with mathutils and Blender's own
// camera conventions, which is two translations of the same math -- so the
// check that matters is not "does it look right" but "does the browser's
// orbitView agree, to the bit". Blender writes camera.json; this runs the
// engine's own orbitView and perspective on those inputs.
//
// It also closes the loop on the FRAME, which is the whole point of the
// feature: Blender's four camera-frame corners, pushed through the engine's
// projView, must land exactly on the edges of clip space. If they do, the
// passepartout in camera view is the game's viewport and nothing else.
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
const camera = JSON.parse(readFileSync(join(OUT, process.argv[2] ?? 'camera.json'), 'utf8'));

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(p => existsSync(p));
if (!CHROME) throw new Error('Chrome not found');

const port = 9733 + (process.pid % 200);
const profile = mkdtempSync(join(tmpdir(), 'pc2-cam-'));
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

// Injected as a CLASSIC script, where `import.meta` is a syntax error and an
// import statement is not allowed -- the same treatment verify_engine.ts gives
// entity.js. perspective and orbitView are pure DOMMatrix and touch neither the
// STRIDE import nor createEngine, so dropping both is safe.
const engineJs = readFileSync(join(REPO, 'src', 'engine.js'), 'utf8')
  .replace(/^export /gm, '')
  .replace(/^import .* from '\.\/pico\.js';\r?$/gm, '')
  .replace(/import\.meta\.env\.DEV/g, 'true');

let failures = 0;
const check = (label: string, ok: boolean, detail = '') => {
  console.log((ok ? '  OK   ' : '  FAIL ') + label + (detail ? '   ' + detail : ''));
  if (!ok) failures++;
};

console.log('\nchecking the Blender camera against src/engine.js');
const got = await evaluate(`(() => {
  ${engineJs}
  const c = ${JSON.stringify(camera)};
  const view = orbitView(c.yaw, c.pitch, c.dist, c.at);
  const projView = perspective(c.fov, c.aspect, c.far).multiply(view);
  // Blender's frame corners are in ENGINE world space already.
  const corners = c.frame.map(p => {
    const v = projView.transformPoint(new DOMPoint(p[0], p[1], p[2], 1));
    return [v.x / v.w, v.y / v.w, v.w];
  });
  return { view: Array.from(view.toFloat64Array()), corners };
})()`);

console.log('\n[1] orbitView agrees with the matrix Blender built');
const worst = Math.max(...camera.view.map((v: number, i: number) => Math.abs(v - got.view[i])));
// 1e-5, not 0: mathutils matrices are SINGLE precision, so a translation of ~17
// carries about 2e-6 of float32 error before either side has done anything
// wrong. The same reason verify_engine.ts compares part matrices at 1e-4.
check('view matrix matches to 1e-5', worst < 1e-5, `max delta ${worst.toExponential(1)}`);

console.log('\n[2] the frame Blender draws is the frame the engine renders');
// Every corner is on an edge of clip space, and in FRONT of the eye. A camera
// with the wrong FOV or the wrong aspect fails here while still looking fine
// in the viewport, which is exactly the failure this is here to catch.
for (const [i, [x, y, w]] of got.corners.entries()) {
  check(`corner ${i} is on the clip-space edge`,
        Math.abs(Math.abs(x) - 1) < 1e-4 && Math.abs(Math.abs(y) - 1) < 1e-4 && w > 0,
        `x ${x.toFixed(6)}  y ${y.toFixed(6)}  w ${w.toFixed(3)}`);
}
const xs = got.corners.map(([x]: number[]) => Math.sign(x.toFixed(6) as unknown as number));
const ys = got.corners.map(([, y]: number[]) => Math.sign(y.toFixed(6) as unknown as number));
check('and the four corners are the four corners',
      new Set(xs).size === 2 && new Set(ys).size === 2,
      `x signs ${xs} y signs ${ys}`);

ws.close();
chrome.kill();
console.log(`\n${failures ? `FAILURES (${failures})` : 'ALL PASS'}`);
process.exit(failures ? 1 : 0);
