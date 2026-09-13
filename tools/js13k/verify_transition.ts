// Visual smoke test for the title/stage rainbow wipe. Run while Vite serves
// http://localhost:5173; screenshots are written to a temporary directory.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
].find(existsSync);
if (!CHROME) throw new Error('Chrome not found');

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const port = 9533 + process.pid % 200;
const out = mkdtempSync(join(tmpdir(), 'rainbow-wipe-'));
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${port}`,
  `--user-data-dir=${mkdtempSync(join(tmpdir(), 'rainbow-cdp-'))}`,
  '--window-size=960,540', '--force-device-scale-factor=1',
  '--no-first-run', '--no-default-browser-check', 'about:blank',
], { stdio: 'ignore' });

try {
  let wsUrl = '';
  for (let i = 0; i < 100 && !wsUrl; i++) {
    try {
      const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      wsUrl = tabs.find((t: any) => t.type === 'page')?.webSocketDebuggerUrl || '';
    } catch {}
    if (!wsUrl) await sleep(100);
  }
  if (!wsUrl) throw new Error('CDP never came up');

  const ws = new WebSocket(wsUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0;
  const pending = new Map<number, (m: any) => void>();
  const errors: string[] = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data as string);
    if (m.id) pending.get(m.id)?.(m);
    if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails));
  };
  const send = (method: string, params: any = {}) => new Promise<any>(resolve => {
    const n = ++id;
    pending.set(n, resolve);
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  const evaluate = async (expression: string) => {
    const m = await send('Runtime.evaluate', { expression, returnByValue: true });
    if (m.result.exceptionDetails) throw new Error(JSON.stringify(m.result.exceptionDetails));
    return m.result.result.value;
  };
  const shot = async (name: string) => {
    const m = await send('Page.captureScreenshot', { format: 'png' });
    const path = join(out, `${name}.png`);
    writeFileSync(path, m.result.data, 'base64');
    return path;
  };

  await send('Runtime.enable');
  await send('Page.enable');
  // localhost, not 127.0.0.1: vite binds IPv6 only here, so the v4 literal
  // never answers and the readiness poll below spins out.
  await send('Page.navigate', { url: 'http://localhost:5173' });
  for (let i = 0; i < 100; i++) {
    if (await evaluate(`typeof transitionState === 'function'`)) break;
    await sleep(50);
  }

  await send('Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'x', code: 'KeyX', windowsVirtualKeyCode: 88,
  });
  await sleep(100);
  const entering = await evaluate('transitionState()');
  const enteringShot = await shot('entering');
  await sleep(330);
  const covered = await evaluate('transitionState()');
  const coveredShot = await shot('covered');
  await sleep(400);
  const revealed = await evaluate('transitionState()');
  const revealedShot = await shot('revealed');

  const ok = entering[0] === 1 && entering[1] && entering[2] === 0
    && covered[0] === 5 && covered[1] && covered[2] === -1
    && revealed[0] === 5 && !revealed[1] && revealed[2] === -1
    && !errors.length;
  console.log(JSON.stringify({ entering, covered, revealed, errors,
    screenshots: [enteringShot, coveredShot, revealedShot] }, null, 2));
  ws.close();
  if (!ok) process.exitCode = 1;
} finally {
  chrome.kill();
}
