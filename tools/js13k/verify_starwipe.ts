// Real wheel click -> STAR wipe, including full cover across the palette swap.
// STAR_SHOT changes the stop time; --mobile tests a phone-sized viewport.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
const baseline = process.argv[2];
const out = mkdtempSync(join(tmpdir(), 'engine-review-'));
const chromePath = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe'].find(existsSync);
if (!chromePath) throw Error('Chrome missing');
const devServer = process.env.ENGINE_DEV_URL ? null : await createServer({
  configFile: false, server: { host: '127.0.0.1', port: 0 },
});
await devServer?.listen();
const devUrl = process.env.ENGINE_DEV_URL || devServer!.resolvedUrls!.local[0];
const server = Bun.serve({ port: 0, hostname: '127.0.0.1', fetch(req) {
  const url = new URL(req.url);
  if (url.pathname === '/baseline') return new Response(Bun.file(baseline), {headers:{'Content-Type':'text/html'}});
  if (url.pathname === '/packed') return new Response(Bun.file('dist13k/index.html'), {headers:{'Content-Type':'text/html'}});
  return new Response('Not found', {status:404});
}});
const port = 9600 + process.pid % 200;
const chrome = spawn(chromePath, ['--headless=new', `--remote-debugging-port=${port}`,
  `--user-data-dir=${out}/profile`, '--window-size=960,540', '--force-device-scale-factor=1',
  '--no-first-run', '--no-default-browser-check', 'about:blank'], {stdio:'ignore'});
const sleep = (ms:number) => new Promise(r=>setTimeout(r,ms));
let ws:WebSocket;
try {
  let url;
  for(let i=0;i<100&&!url;i++) {
    try { url=(await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page')?.webSocketDebuggerUrl; } catch {}
    if(!url) await sleep(50);
  }
  if(!url) throw Error('Chrome startup timeout');
  // Every await below is settled ONLY by an incoming message, so a socket that
  // dies -- Chrome crashing, being killed, losing its target -- would leave the
  // run waiting forever INSIDE the try, where the finally that kills Chrome and
  // stops the servers is never reached. onopen alone has the same hole. So the
  // socket's failure paths are wired to fail the run instead: a thrown error
  // unwinds into the finally, which is what cleans up.
  ws = new WebSocket(url);
  let dead:(e:Error)=>void = () => {};
  const closed = new Promise<never>((_,j)=>{dead=j});
  closed.catch(()=>{});   // it is only ever awaited via race; never unhandled
  ws.onclose=()=>dead(Error('CDP socket closed -- did Chrome exit?'));
  ws.onerror=()=>dead(Error('CDP socket error'));
  await Promise.race([new Promise(r=>ws.onopen=r), closed]);
  let id=0;
  const pending=new Map(); let errors=[];
  ws.onmessage=e=>{const m=JSON.parse(String(e.data)); if(m.id){pending.get(m.id)?.(m);pending.delete(m.id)}
    if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails)};
  const send=(method,params={})=>Promise.race([closed,new Promise<any>((r,j)=>{let n=++id;pending.set(n,m=>m.error?j(Error(JSON.stringify(m.error))):r(m.result));ws.send(JSON.stringify({id:n,method,params}))})]);
  const evaluate=async(expression)=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`
    window.__time=1000; Math.random=()=>.5; performance.now=()=>__time;
    window.__raf=[]; requestAnimationFrame=f=>(__raf.push(f),__raf.length);
    window.__step=t=>{__time=t; const q=__raf.splice(0);for(const f of q)f(t)};
    window.__loaded=false;addEventListener('load',()=>__loaded=true);
  `});
  async function runStar(name, target) {
    errors = [];
    await send('Page.navigate', {url: target});
    let ready = false;
    for (let i=0;i<100;i++) {
      if(await evaluate('window.__loaded && __raf.length > 0')) {ready=true;break;}
      await sleep(30);
    }
    if(!ready) throw Error('Game failed to initialize: '+JSON.stringify(errors));
    if(name==='dev') await evaluate(`(()=>{const flush=H.flush;H.flush=m=>{
      flush(m);const g=H.gl;if(!m.isIdentity)return;
      const b=new Uint8Array(g.drawingBufferWidth*g.drawingBufferHeight*4);
      g.readPixels(0,0,g.drawingBufferWidth,g.drawingBufferHeight,g.RGBA,g.UNSIGNED_BYTE,b);
      let holes=0;const colors=new Set();for(let i=0;i<b.length;i+=4){if(b[i+3]!==255)holes++;else colors.add(b.slice(i,i+3).join(','));}
      window.__starCoverage={holes,colors:colors.size};};})()`);
    const step = t => evaluate(`__step(${t})`);
    const shot = async label => {
      const r=await send('Page.captureScreenshot',{format:'png'});
      writeFileSync(join(out,`${name}-${label}.png`),Buffer.from(r.data,'base64'));
      return r.data;
    };
    await step(1000);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'x',code:'KeyX',windowsVirtualKeyCode:88});
    const offset = Number(process.env.STAR_SHOT || 2000) - 2000;
    await step(1400); await step(1800); await step(2000 + offset);
    // Same screen location in dev and packed, determined from the actual wheel
    // placement in the dev pass. Exercise the real pointer handler in both.
    if(name==='dev') globalThis.wheelPoint = await evaluate(`(()=>{
      const o=objects.find(o=>o.parts.length===${(await import('../../src/entities.js')).COLOR_WHEEL.length});
      const p=projView.transformPoint(o.place.transformPoint(new DOMPoint(0,1.25,0)));
      const r=document.querySelector('canvas').getBoundingClientRect();
      return {x:r.x+(p.x/p.w+1)*r.width/2,y:r.y+(1-p.y/p.w)*r.height/2};
    })()`);
    const point=globalThis.wheelPoint;
    await send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
    await step(5999 + offset);
    if(name==='dev' && await evaluate('!!starWipeState()')) throw Error('Wipe started before wheel settled');
    await step(6000 + offset);
    for(let beat=0;beat<8;beat++) {
      await step(6000 + offset + beat*200);
      if(name==='dev') {
        if(await evaluate('!!starWipeState()')) throw Error('Wipe started before four blinks finished');
        const correct=await evaluate(`objects.find(o=>o.parts.length===${(await import('../../src/entities.js')).COLOR_WHEEL.length}).parts[16].color === (${beat}%2 ? 0 : prize[0]+16)`);
        if(!correct) throw Error('Wrong indicator color on blink beat '+beat);
      }
    }
    await step(7599 + offset);
    if(name==='dev' && await evaluate('!!starWipeState()')) throw Error('Wipe cut the last blink short');
    await step(7600 + offset);
    if(name==='dev' && !await evaluate('!!starWipeState()')) throw Error('Wheel shot did not trigger the star wipe');
    const shots=[];
    for(const [t,label] of [[6000,'start'],[6050,'growing'],[6250,'covering'],[6720,'covered-before'],
      [6800,'covered-swap'],[6880,'covered-after'],[7350,'shrinking'],[7575,'tiny'],[7600,'finished']]) {
      await step(t + offset + 1600);
      shots.push(await shot(label));
      if(name==='dev' && label.startsWith('covered')) {
        console.log(label, await evaluate('starWipeState()'));
        const coverage=await evaluate('__starCoverage');
        if(coverage.holes) throw Error('Star does not cover the frame: '+JSON.stringify(coverage));
        if(coverage.colors !== 1) throw Error('Star should be one flat color');
        console.log('flat cover',coverage);
        const before=await evaluate('JSON.stringify(transitionState())');
        await send('Input.dispatchKeyEvent',{type:'keyDown',key:'x',code:'KeyX',windowsVirtualKeyCode:88});
        if(before!==await evaluate('JSON.stringify(transitionState())')) throw Error('Input restarted the active wipe');
      }
    }
    if(name==='dev' && await evaluate('!!starWipeState()')) throw Error('Wipe never finished');
    if(errors.length)throw Error(JSON.stringify(errors));
    return shots;
  }
  if(process.argv.includes('--mobile')) await send('Emulation.setDeviceMetricsOverride',
    {width:375,height:900,deviceScaleFactor:1,mobile:false});
  const dev=await runStar('dev',devUrl);
  const packed=await runStar('packed',`http://127.0.0.1:${server.port}/packed`);
  // Decode screenshots in Chrome, compare dev/packed and inspect the canvas
  // rectangle at the cover plateau (outside the portrait frame stays black).
  const result=await evaluate(`(async()=>{
    const sets=${JSON.stringify([dev,packed])};
    const decode=async src=>{const b=await createImageBitmap(await(await fetch('data:image/png;base64,'+src)).blob());
      const c=new OffscreenCanvas(b.width,b.height),g=c.getContext('2d');g.drawImage(b,0,0);return {w:b.width,h:b.height,p:g.getImageData(0,0,b.width,b.height).data};};
    const rect=document.querySelector('canvas').getBoundingClientRect(),diff=[],covers=[];
    for(let i=0;i<sets[0].length;i++){
      const a=await decode(sets[0][i]),b=await decode(sets[1][i]);let n=0;
      for(let j=0;j<a.p.length;j+=4)if(a.p[j]!==b.p[j]||a.p[j+1]!==b.p[j+1]||a.p[j+2]!==b.p[j+2])n++;
      diff.push(n);
      if(i>=3&&i<=5){let bad=0;const k=(Math.floor(rect.y+rect.height/2)*a.w+Math.floor(rect.x+rect.width/2))*4;
        for(let y=Math.ceil(rect.y);y<Math.floor(rect.bottom);y++)for(let x=Math.ceil(rect.x);x<Math.floor(rect.right);x++){
          const j=(y*a.w+x)*4;if(a.p[j]!==a.p[k]||a.p[j+1]!==a.p[k+1]||a.p[j+2]!==a.p[k+2])bad++;}
        covers.push({shadedPixels:bad,color:[...a.p.slice(k,k+3)]});}
    }
    return {diff,covers};
  })()`);
  console.log(JSON.stringify({out,...result},null,2));
  if(result.diff.some(n=>n) ||
    result.covers.some(c=>String(c.color)!==String(result.covers[0].color))) throw Error('Star wipe pixel verification failed');
  console.log('PASS: star covers the frame, holds its colour across the swap, and dev matches packed');
}finally{
  ws?.close();
  // chrome.kill() kills the LAUNCHER, and on Windows the real browser
  // processes are its children -- they survive, keep their sockets open, and
  // then both servers below wait for those connections forever. The work is
  // finished by this point and every line above has been printed, so the run
  // looks like a hang with no output at all (stdout is buffered until exit).
  // Kill the tree, and force the sockets shut rather than draining them.
  if (process.platform === 'win32') spawn('taskkill', ['/pid', String(chrome.pid), '/T', '/F'], {stdio:'ignore'});
  else chrome.kill();
  server.stop(true);
  // NOT awaited, and that is the whole point: Vite's close() does not resolve
  // here (instrumented, Sep 2026 -- the run reaches this line and never leaves
  // it), and process.exit below is AFTER it, so awaiting made the one line that
  // guarantees an exit unreachable. That is the hang this script kept leaving
  // behind: every result printed, the exit code set, and the process still
  // alive hours later. Ask it to close, then go -- exiting takes the handles
  // with it, and a rejection here would be about a server we are abandoning
  // on purpose.
  devServer?.close().catch(() => {});
}
// And then LEAVE. Killing Chrome's tree and forcing the sockets shut was not
// enough on its own -- a run still sat there after printing its last line, so
// something (Vite's own watchers and ws server are the likely candidates) keeps
// a handle alive that nothing here owns. Every result is printed and the exit
// code is set by this point, so waiting for the event loop to drain buys
// nothing and costs a run that never returns. This is the line that makes
// `bun run test:engine` finish -- keep every await above it bounded, or it
// stops being reachable.
process.exit(process.exitCode ?? 0);

