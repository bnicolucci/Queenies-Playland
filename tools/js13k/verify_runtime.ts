// Deterministic packed/dev rendering and numerical regression checks.
// Pass a saved pre-change packed index.html to compare it with the current build.
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
  const run=async(name,url)=>{
    errors=[];await send('Page.navigate',{url});
    let ready = false;
    for(let i=0;i<100;i++){if(await evaluate('window.__loaded && __raf.length>0')){ready=true;break}await sleep(30)}
    if (!ready) throw Error(`Game did not initialize: ${url}`);
    const shots=[];
    const frame=async(t,label)=>{
      await evaluate(`__step(${t})`);
      const shot=await send('Page.captureScreenshot',{format:'png'});
      const buffer=Buffer.from(shot.data,'base64');
      writeFileSync(join(out,`${name}-${label}.png`),buffer);
      shots.push(shot.data);
    };
    await frame(1000,'title');
    await send('Input.dispatchMouseEvent',{type:'mousePressed',x:480,y:240,button:'left',clickCount:1});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:480,y:240,button:'left',clickCount:1});
    for(const t of [1060,1120,1240,1600,2400]) await frame(t,`broken-${t}`);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'x',code:'KeyX',windowsVirtualKeyCode:88});
    for(const t of [2500,2770,2900,3200,3600])await frame(t,`stage-${t}`);
    // Scan the scene with genuine pointer events: exercise hits, bounces,
    // stage changes, sound caching and the corresponding HUD counter.
    let t=3600;
    for(let y=100;y<450;y+=70)for(let x=120;x<850;x+=90){
      await send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
      await evaluate(`__step(${t+=100})`);
    }
    await frame(t+1000,'after-clicks');
    if(errors.length)throw Error(JSON.stringify(errors));
    return shots;
  };
  let ref;
  if(baseline) ref=await run('baseline',`http://127.0.0.1:${server.port}/baseline`);
  const packed=await run('packed',`http://127.0.0.1:${server.port}/packed`);
  const dev=await run('dev',devUrl);
  console.log('bounds:', await evaluate(`(async()=>{
    const {worldBounds,trs}=await import('/src/entity.js');
    let seed=13,worst=0;const random=()=>((seed=Math.imul(seed,1664525)+1013904223|0)>>>0)/4294967296;
    const vector=(s)=>Array.from({length:3},()=>random()*s-s/2);
    for(let i=0;i<1000;i++){
      const place=trs(vector(20),vector(360),vector(5));
      const parts=Array.from({length:4},()=>({mesh:{min:vector(2).map(v=>v-2),max:vector(2).map(v=>v+2)},local:trs(vector(10),vector(360),vector(5))}));
      const actual=worldBounds(parts,place),lo=[Infinity,Infinity,Infinity],hi=lo.map(v=>-v);
      for(const p of parts)for(let c=0;c<8;c++){
        const v=place.multiply(p.local).transformPoint(new DOMPoint(...[0,1,2].map(k=>(c>>k&1?p.mesh.max:p.mesh.min)[k])));
        [v.x,v.y,v.z].forEach((n,k)=>{lo[k]=Math.min(lo[k],n);hi[k]=Math.max(hi[k],n)});
      }
      for(let k=0;k<3;k++)worst=Math.max(worst,Math.abs(lo[k]-actual.min[k]),Math.abs(hi[k]-actual.max[k]));
    }
    if(worst>1e-10)throw Error('Bounds mismatch '+worst);
    const empty=worldBounds([{local:new DOMMatrix()}],new DOMMatrix());
    if(empty.min[0]!==Infinity||empty.max[0]!==-Infinity)throw Error('Pivot bounds changed');
    return {cases:1000,worst};
  })()`));
  for(const [name,other] of [['baseline',ref],['dev',dev]])if(other){
    const mismatches=[];
    for(let i=0;i<packed.length;i++)if(packed[i]!==other[i]) {
      const count=await evaluate(`(async()=>{
        const read=async(data)=>{const im=new Image();im.src='data:image/png;base64,'+data;await im.decode();const c=new OffscreenCanvas(im.width,im.height);const ctx=c.getContext('2d');ctx.drawImage(im,0,0);return ctx.getImageData(0,0,im.width,im.height).data};
        const a=await read(${JSON.stringify(packed[i])}),b=await read(${JSON.stringify(other[i])});let n=0;
        for(let k=0;k<a.length;k+=4)if(a[k]!==b[k]||a[k+1]!==b[k+1]||a[k+2]!==b[k+2])n++;
        return n;
      })()`);
      if(count)mismatches.push({frame:i,pixels:count});
    }
    console.log(`${name} vs packed: ${packed.length-mismatches.length}/${packed.length} identical frames`,mismatches);
    if(mismatches.length)process.exitCode=1;
  }
  // The presentation rectangle must not change the camera or pointer mapping.
  // Exercise height-limited, width-limited, exact-size and high-DPI windows.
  let pixelDensity;
  for (const [width,height,deviceScaleFactor] of [[600,800,1],[1200,800,1],[375,900,3],[1200,500,2]]) {
    await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor,mobile:false});
    await send('Page.navigate',{url:devUrl});
    let ready=false;
    for(let i=0;i<100;i++){if(await evaluate('window.__loaded && __raf.length>0')){ready=true;break}await sleep(30)}
    if(!ready)throw Error('Viewport test initialization timed out');
    const rect=await evaluate(`(()=>{
      __step(1000);
      const c=document.querySelector('canvas'),r=c.getBoundingClientRect();
      if(Math.abs(r.width/r.height-.75)>1e-5)throw Error('Wrong game aspect');
      if(r.width>innerWidth+.1||r.height>innerHeight+.1)throw Error('Game clipped');
      if(Math.abs(r.left*2+r.width-innerWidth)>.1||Math.abs(r.top*2+r.height-innerHeight)>.1)throw Error('Not centered');
      return {x:r.x,y:r.y,width:r.width,height:r.height};
    })()`);
    const buffer=await evaluate(`(()=>{const c=document.querySelector('canvas');return [c.width,c.height]})()`);
    pixelDensity ??= buffer[0]/rect.width;
    const expected=[rect.width,rect.height].map(n=>Math.ceil(n*deviceScaleFactor*pixelDensity));
    if(String(buffer)!==String(expected))throw Error('Render resolution did not follow displayed device pixels');
    const shot=await send('Page.captureScreenshot',{format:'png'});
    writeFileSync(join(out,`viewport-${width}x${height}.png`),Buffer.from(shot.data,'base64'));
    // Leave the title through its real keyboard handler, then isolate one
    // existing object and aim at its bounds, independent of authored layout.
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'x',code:'KeyX',windowsVirtualKeyCode:88});
    await evaluate(`(()=>{
      __step(1400);__step(1800);
      const o=objects.find(o=>o.parts.some(p=>p.mesh));
      objects.splice(0,objects.length,o);o.t='bonus';o.bounce=0;
      cam.at=o.min.map((v,i)=>(v+o.max[i])/2);cam.shake=0;
      __step(1900);
    })()`);
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:rect.x+rect.width/2,y:rect.y+rect.height/2,button:'left',clickCount:1});
    if(!await evaluate('objects[0].bounce===1'))throw Error('Picking missed after resize');
    // Black bars must neither shoot nor move the target. Test an actual bar
    // whenever this window leaves one, including touch's captured release.
    if(rect.x>1||rect.y>1){
      await evaluate('objects[0].bounce=0');
      const x=rect.x>1?rect.x/2:width/2,y=rect.y>1?rect.y/2:height/2;
      await send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
      await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:rect.x+rect.width/2,y:rect.y+rect.height/2}]});
      await send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y}]});
      await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      if(await evaluate('!!objects[0].bounce'))throw Error('Black margin accepted a shot');
    }
    console.log('viewport/picking:',{width,height,deviceScaleFactor,rect});
  }
  await send('Page.navigate',{url:devUrl});
  let hudReady=false;
  for(let i=0;i<100;i++){if(await evaluate('window.__loaded && __raf.length>0')){hudReady=true;break}await sleep(30)}
  if(!hudReady)throw Error('HUD scale test initialization timed out');
  console.log('independent HUD scaling:',await evaluate(`(()=>{
    // Compare actual submitted glyph/effect transforms, not a second layout
    // implementation. Changing sample density must leave every command intact.
    const draw=H.draw;
    let commands=[];
    H.draw=(mesh,matrix,color,uv)=>{
      commands.push([Array.from(matrix.toFloat64Array()),color,uv]);
      draw(mesh,matrix,color,uv);
    };
    const capture=(world,hud)=>{
      setRenderScale(world,hud);commands=[];__step(1000);
      return {layout:JSON.stringify(commands),
        world:document.querySelector('canvas').toDataURL(),
        hud:document.querySelector('#hud').toDataURL()};
    };
    try {
      const base=capture(4,2);
      for(const [world,hud] of [[1,2],[8,2],[4,1],[4,4]]){
        const got=capture(world,hud);
        if(got.layout!==base.layout)throw Error('HUD layout changed with render scale');
        if(hud===2&&got.hud!==base.hud)throw Error('World scale changed HUD pixels');
        if(world===4&&got.world!==base.world)throw Error('HUD scale changed world pixels');
        if(hud!==2&&got.hud===base.hud)throw Error('HUD detail did not change');
      }
      return {scalePairs:5,identicalLayout:true,independentPixels:true};
    } finally {H.draw=draw;}
  })()`));
  console.log('Screenshots:',out);
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
