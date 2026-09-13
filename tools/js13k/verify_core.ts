// Pure parser/compact-format and fixed-volume audio checks; no browser needed.
import assert from 'node:assert/strict';
import { parsePicoCAD } from '../../src/pico.js';
import { renderSfx, SOUNDS } from '../../src/sfx.js';
import { tryEncodeCompact } from '../vite/picocad_compact.ts';
const raw=await Bun.file('src/assets/model.txt').text();
const model=JSON.parse(raw);
const original=parsePicoCAD(raw);
const compact=parsePicoCAD(tryEncodeCompact(raw)!);
assert.deepEqual(original.texture,compact.texture);
assert.deepEqual(original.palette,compact.palette);
assert.deepEqual(original.shades,compact.shades);
let maxError=0;
for(let i=0;i<original.objects.length;i++){
 const a=original.objects[i], b=compact.objects[i];
 assert.equal(a.name,b.name); assert.equal(a.data.length,b.data.length);
 for(let j=0;j<a.data.length;j++){
  const d=Math.abs(a.data[j]-b.data[j]);
  if(j%10>=8)assert.equal(d,0,'Color/flag drift');
  else if(j%10>=6)assert(d<.0051/128,'UV rounding exceeds half the compact texel precision');
  else maxError=Math.max(maxError,d);
 }
}
assert(maxError<1e-4);
// A nested, rotated, non-uniformly scaled triangle. Expected positions use
// scalar scale/X/Y/Z rotation/translation, independent of the matrix builder.
const transform=(p,t)=>{
 let [x,y,z]=p.map((n,i)=>n*t.scale['xyz'[i]]);
 let c=Math.cos(t.rot.x),s=Math.sin(t.rot.x);[y,z]=[c*y-s*z,s*y+c*z];
 c=Math.cos(t.rot.y);s=Math.sin(t.rot.y);[x,z]=[c*x+s*z,-s*x+c*z];
 c=Math.cos(t.rot.z);s=Math.sin(t.rot.z);[x,y]=[c*x-s*y,s*x+c*y];
 return [x+t.pos.x,y+t.pos.y,z+t.pos.z];
};
const parent={pos:{x:2,y:-3,z:4},rot:{x:.43,y:-.72,z:1.3},scale:{x:.8,y:1.3,z:2}};
const child={pos:{x:-1,y:.6,z:2},rot:{x:-.31,y:.27,z:.83},scale:{x:1.2,y:.7,z:1.9}};
const points=[[0,0,0],[1,0,0],[0,1,1]];
model.graph={visible:true,transform:parent,children:[{name:'triangle',visible:true,transform:child,children:[],mesh:{vertices:points.flat(),faces:[{vertex_ids:[1,2,3],uvs:[0,0,1,0,0,1],color:2,noshade:false,notex:true}]}}]};
const result=parsePicoCAD(JSON.stringify(model)).objects[0].data;
for(let i=0;i<3;i++){
 const expected=transform(transform(points[i],child),parent);expected[0]*=-1;
 for(let k=0;k<3;k++)assert(Math.abs(result[i*10+k]-expected[k])<2e-6,'TRS mismatch');
}
let samples=0;
const random=Math.random;
function seeded(){let seed=17;Math.random=()=>((seed=Math.imul(seed,1664525)+1013904223|0)>>>0)/4294967296;}
try{
 for(const sound of SOUNDS){
  seeded();const full=renderSfx(sound);
  seeded();const half=renderSfx(sound,.35);
  assert.equal(full.length,half.length);
  for(let i=0;i<full.length;i++)assert.equal(half[i],full[i]*.5,'Audio volume changed');
  samples+=full.length;
 }
}finally{Math.random=random;}
console.log(`PASS: ${original.objects.length} raw/compact meshes (max rounding ${maxError}), nested TRS, ${samples} audio samples at identical playback volume`);
