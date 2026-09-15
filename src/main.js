
import * as A from './anim.js';
import modelTxt from './assets/model.txt?raw';
import { makeCamera } from './camera.js';
import { createEngine, perspective } from './engine.js';
import { CARNY, COLOR_WHEEL, EYE_COMPLEX, MALLET, MARK, MOLE, STAR, TIMER, UNICORN, WATERGUN, WHACKA, X } from './entities.js';
import { drawEntity, poseState, spawnEntity, trs, worldBounds } from './entity.js';
import { GAME_VIEW } from './game_view.js';
import { buildPalette, parsePicoCAD } from './pico.js';
import { bounce_lite, carnysound, playSfx, SOUNDS, stopSfx, water_spray, whack, wheel_rotate } from './sfx.js';
import frag from './shaders/model.frag?raw';
import vert from './shaders/model.vert?raw';
import { COLOR_CHOOSER, INTRO, STAGE_1, STAGE_2 } from './stages.js';

const DEBUG = false;

// The carny's face is one pose state per expression, each carrying the mouth
// override (MOUTH_ROT is written per frame) plus one of EYE_COMPLEX's authored
// eye states re-based onto the carny's two inlined eyes: eye part k lives at
// CARNY[10 + k] (right) and CARNY[18 + k] (left). The mouth rides in EVERY
// state so tweening between two of them never drags it back to the blueprint.
const MOUTH_ROT = [0, 0, 0];
const carnyFace = eyes => [-1, [[3, 0, MOUTH_ROT, 0],
  ...(eyes < 0 ? [] : EYE_COMPLEX.s[eyes][1].flatMap(([k, ...v]) => [[10 + k, ...v], [18 + k, ...v]]))]];
const CARNY_CALM = carnyFace(-1), CARNY_SHUT = carnyFace(0), CARNY_ANGRY = carnyFace(1);
// timeline() steps: [state, ms to get there, ms to hold]. Blink repeats;
// the other two play once from their trigger and park on the last state.
const EYES_BLINK = [[CARNY_SHUT, 80, 60], [CARNY_CALM, 120, 3300]];
const EYES_SHOT = [[CARNY_ANGRY, 60, 340], [CARNY_CALM, 200, 0]], EYES_SHOT_MS = 600;
const EYES_LAUGH = [[CARNY_SHUT, 150, 1e9]];

const WHEEL_PIVOT = 17;
const WHEEL_SELECT = 16;
const WHEEL_RATE = 0.3;
const WHEEL_STOP = 4000;
const WHEEL_AXIS = 2;
const WHEEL_ROT = [...(COLOR_WHEEL[WHEEL_PIVOT].rot || [0, 0, 0])];
const EMISSIVE = 16;
const WHEEL_PULSE = 1.35;
const WHEEL_PULSE_MS = 200;
const WHEEL_BEAT = 200;
const WHEEL_BLINKS = 4;
const WHEEL_BASE = [...(COLOR_WHEEL[WHEEL_SELECT].scale || [1, 1, 1])];
const WHEEL_SCALE = [...WHEEL_BASE];
const WHEEL_STATE = [-1, [[WHEEL_PIVOT, 0, WHEEL_ROT, 0], [WHEEL_SELECT, 0, 0, WHEEL_SCALE]]];
const WHEEL_BLINK = [-1, [...WHEEL_STATE[1], ...COLOR_WHEEL.s[0][1]]];
const BLINK_STEPS = [[WHEEL_BLINK, 80, 60, A.ease_out_quad], [WHEEL_STATE, 120, 2800, A.ease_in_quad]];

const WEDGES = COLOR_WHEEL.filter((p, i) => {
  while (i != null && i !== WHEEL_PIVOT) i = COLOR_WHEEL[i].parent;
  return i === WHEEL_PIVOT && p.mesh === 'mesh_slice';
});
if (import.meta.env.DEV) {
  const pivot = COLOR_WHEEL[WHEEL_PIVOT];
  if (pivot.mesh)
    throw new Error(`WHEEL_PIVOT ${WHEEL_PIVOT} names a '${pivot.mesh}' part; the spin belongs on the wheel_rot pivot`);
  if (!COLOR_WHEEL[WHEEL_SELECT].mesh)
    throw new Error(`WHEEL_SELECT ${WHEEL_SELECT} names a pivot, not the pointer`);
  if (!WEDGES.length)
    throw new Error(`no mesh_slice wedges hang off part ${WHEEL_PIVOT}`);
  const still = v => !v || v.every(n => n === 0);
  for (let i = pivot.parent; i != null; i = COLOR_WHEEL[i].parent) {
    const p = COLOR_WHEEL[i];
    if (!still(p.rot))
      throw new Error(`part ${i} sits above the wheel pivot and is rotated (${p.rot}) -- that re-aims the dial`);
    if (p.scale && p.scale.some(n => n !== 1))
      throw new Error(`part ${i} sits above the wheel pivot and is scaled (${p.scale}) -- that can shear the dial`);
  }
}


const topWedge = a => {
  let best = 999, hit;
  for (const p of WEDGES) {
    if (won.includes(p.color)) continue;
    const s = ((((p.rot ? p.rot[1] : 0) - 180 - a) % 360) + 540) % 360 - 180;
    if (Math.abs(s) < best) { best = Math.abs(s); hit = [p.color, a + s]; }
  }
  return hit;
};
let wheelHit = 0, wheelFrom = 0, wheelTo = 0, wheelColor = -1, wheelSpin = 0, bounceSpin = 0, wheel;
let prize = -1, wheelChosenAt = 0;
const won = [];
const stopWheel = () => {
  wheelHit = performance.now();
  wheelFrom = -wheelHit * WHEEL_RATE;
  [, wheelTo] = topWedge(wheelFrom - WHEEL_RATE * WHEEL_STOP / 2);
};

const COLOR_NAMES = { 1: 'WHITE', 2: 'YELLOW', 4: 'ORANGE', 6: 'RED', 8: 'GREEN', 10: 'BLUE', 12: 'INDIGO', 14: 'PURPLE' };
const RAINBOW = [6, 5, 4, 2, 7, 10, 13], FRAY = 44, SPREAD = 64, SKEW = 15;

let EASE = 'ease_out_quad';

const canvas = document.querySelector('canvas');
const hudCanvas = document.querySelector('#hud');

const model = parsePicoCAD(modelTxt);

let GLYPHS = "0123456789LISQUEN'PAYDOW!";
let FONT = '7cc6cefee6c67c00307030303030fc007cc6063c60c6fe007cc6061c06c67c00'
  + '1c3c6cccfe0c1e00fec0c0fc06c67c003c60c0fcc6c67c00fec6060c18303000'
  + '7cc6c67cc6c67c007cc6c67e060c7800c0c0c0c0c0c0fe007830303030307800'
  + '7cc6c07c06c67c007cc6c6c6c6ce7c0ec6c6c6c6c6c67c00fec0c0fcc0c0fe00'
  + 'c6e6f6decec6c6003830600000000000fcc6c6fcc0c0c0007cc6c6fec6c6c600'
  + 'cccc783030303000f8ccc6c6c6ccf800'
  + '7cc6c6c6c6c67c00c6c6c6d6feeec6003030303030003000';
if (import.meta.env.DEV) {
  GLYPHS += 'FC';
  FONT += 'fec0c0fcc0c0c0007cc6c0c0c0c67c00';
}
const BANDS = [10, 2, 4, 15];
const HUD = 0, YELLOW = 1, ORANGE = 2, DEAD = 3;
const FONT_Y = 112;
const bandY = b => FONT_Y - b * 16;
for (let b = 0; b < BANDS.length; b++)
  for (let i = 0; i < GLYPHS.length; i++)
    for (let r = 0; r < 8; r++) {
      const v = parseInt(FONT.substr(i * 16 + r * 2, 2), 16);
      for (let c = 0; c < 8; c++)
        if (v >> (7 - c) & 1)
          model.texture.pixels[(bandY(b) + (i >> 4) * 8 + r) * 128 + (i & 15) * 8 + c] = BANDS[b];
    }

const E = createEngine(canvas, vert, frag);
const H = createEngine(hudCanvas, vert, frag);
if (import.meta.env.DEV) window.E = E;
E.setModel(model);
H.setModel(model);
H.gl.disable(H.gl.DEPTH_TEST);

const MESH = Object.fromEntries(model.objects.map(o => [o.name, E.mesh(o)]));
const glyphQuad = (() => {
  const u1 = 8 / 128, v0 = FONT_Y / 128, v1 = (FONT_Y + 8) / 128;
  const vtx = (x, y, u, v) => [x, y, 0, 0, 0, 1, u, v, 0, 1];
  return H.mesh({ data: new Float32Array([
    ...vtx(0, 0, 0, v0), ...vtx(8, 0, u1, v0), ...vtx(8, 8, u1, v1),
    ...vtx(0, 0, 0, v0), ...vtx(8, 8, u1, v1), ...vtx(0, 8, 0, v1),
  ]) });
})();

const hudRect = (x, y, w, h, c, uv) => H.draw(glyphQuad,
  new DOMMatrix().translate(x, y, 0).scale(w / 8, h / 8, 1), c, uv);

const drawText = (str, x, y, s = 1, b = HUD, r = 1e9) => {
  const w = (str.length * 8 - 1) * s;
  const sag = r * (1 - Math.cos(w / 2 / r));
  for (let i = 0; i < str.length; i++) {
    const g = GLYPHS.indexOf(str[i]);
    const a = (i * 8 * s + 4 * s - w / 2) / r;
    if (g >= 0) H.draw(glyphQuad, new DOMMatrix()
      .translate(x + w / 2 + r * Math.sin(a), y + 4 * s + r * (1 - Math.cos(a)) - sag, 0)
      .rotate(a * 57.29578)
      .scale(s, s, 1).translate(-4, -4, 0), -1,
      { rect: [(g & 15) * 8 / 128, (bandY(b) + (g >> 4) * 8) / 128, 8 / 128, 8 / 128] });
  }
};

const hash = i => Math.abs(Math.sin(i * 12.9898) * 43758.5) % 1;

const GUN_PIVOT = 0;
const GUN_AIM = WATERGUN.findIndex((p, i) => i && !p.mesh);
const GUN_SPOUT = WATERGUN.findIndex((p, i) => i > GUN_AIM && !p.mesh);
const SPOUT_SWELL = 1.2, SPOUT_MS = 90, SPOUT_SCALE = [1, 1, 1];
const GUN_SWING = 90, GUN_TILT = 60;
const GUN_ROT = [0, 0, 0], GUN_STATE = [-1, [[GUN_PIVOT, 0, GUN_ROT, 0], [GUN_SPOUT, 0, 0, SPOUT_SCALE]]];
const clamp = (v, m) => Math.max(-m, Math.min(m, v));
const DROP_MS = 30, DROPS = 40, SPEED = 14, GRAVITY = 12, DROP_SIZE = 0.18;
const drops = [];
const DEAL_MS = 1000, HIT_GRACE = 300;
const WIN_STAGE = 99;
let others = [], deals = 0, dealAt = 0, hitAt = 0;
const winStage = () => {
  if (swipe) return;
  won.push(prize);
  goStage(won.length === WEDGES.length ? WIN_STAGE : 0);
};
const miss = () => { cam.shake = 2; playSfx(SOUNDS[TAGS.enemy[0]]); };
const deal = () => {
  deals++;
  for (const o of objects)
    if (o.t === 'target') o.color = (o.n + deals) % 3 ? others[hash(o.n + deals * 7) * others.length | 0] : prize;
};
const splash = (o, now) => {
  hitAt = now;
  if (o.color === prize) {
    o.color = 15; o.t = 0; o.bounce = 1;
    playSfx(SOUNDS[TAGS.target[0]]);
    if (!objects.some(o => o.t === 'target')) winStage();
  } else {
    miss();
    dealAt = now; deal();
  }
};
let gun, held = 0, grab, spraying = 0, sprayLoop = 0, lastDrop = 0, spray = water_spray;
const TIMER_S = 30, TIMER_FIT = 20;
let timer, stageAt = 0;
const SLIDE_MS = 1000, LOSE_MS = 2500;
let carny, carnyTo, lost = 0;
// Every opening of the carny's mouth goes through gasp(): a drop landing on
// him opens the pivot once over 2 * GASP_MS, and the lose laugh is the same
// thing on a slower LAUGH_MS clock, re-armed every cycle. Not retriggered
// mid-gasp, so a sustained spray reads as repeated gasps rather than a mouth
// held half open -- and the sound plays once per opening, never per drop.
const GASP_MS = 220, GASP_DEG = -16, LAUGH_MS = 300, LAUGH_DEG = -20;
let gaspAt = -1e9;
const gasp = (now, ms) => { if (now - gaspAt > 2 * ms) { gaspAt = now; playSfx(carnysound); } };
const drawTimer = now => {
  const toHud = (x, y, z) => {
    const p = projView.transformPoint(timer.place.transformPoint(new DOMPoint(x, y, z)));
    return [(p.x / p.w + 1) / 2 * hudW, (1 - p.y / p.w) / 2 * hudH];
  };
  const [cx, cy] = toHud(0, 0, 0), [rx] = toHud(1, 0, 0);
  const s = Math.max(1, (rx - cx) * 2 / TIMER_FIT | 0);
  const left = stageAt ? Math.max(0, TIMER_S - (now - stageAt) / 1000) | 0 : TIMER_S;
  if (stageAt && !left && !lost) { lost = now; held = spraying = 0; }
  drawText(String(left).padStart(2, 0), cx - 7.5 * s | 0, cy - 3.5 * s | 0, s, YELLOW);
};
if (import.meta.env.DEV) {
  import('./sfx_bank.js').then(b => window.bank = b);
  window.setSpray = h => {
    if (typeof h !== 'string') return console.warn('setSpray: not a sound; bank has', Object.keys(bank || {}).join(' '));
    spray = h; sprayLoop = stopSfx(sprayLoop);
  };
}
const muzzle = () => {
  const at = i => {
    const p = gun.place.multiply(gun.parts[i].local).transformPoint(new DOMPoint());
    return [p.x, p.y, p.z];
  };
  const o = at(GUN_AIM), h = at(GUN_PIVOT);
  const d = o.map((v, i) => v - h[i]), n = Math.hypot(...d) / SPEED;
  return [o, d.map(v => v / n)];
};
const drawGun = now => {
  SPOUT_SCALE[0] = SPOUT_SCALE[1] = spraying ? A.tween(now, SPOUT_MS, 1, SPOUT_SWELL, A.ease_out_quad, A.cycle) : 1;
  poseState(gun.parts, WATERGUN, GUN_STATE);
  if (spraying) sprayLoop ||= playSfx(spray, 1);
  else sprayLoop = stopSfx(sprayLoop);
  const k = now / DROP_MS | 0;
  if (spraying) {
    const [o, v] = muzzle();
    for (let i = Math.max(lastDrop + 1, k - DROPS + 1); i <= k; i++) drops[i % DROPS] = [i, ...o, ...v];
  }
  lastDrop = k;
  for (const d of drops) {
    if (!d || k - d[0] >= DROPS) continue;
    const age = (now - d[0] * DROP_MS) / 1000;
    const p = [d[1] + d[4] * age, d[2] + d[5] * age - GRAVITY * age * age / 2, d[3] + d[6] * age];
    if (now - hitAt > HIT_GRACE)
      for (const o of objects)
        if ((o.t === 'target' || o === carny) && p.every((v, k) => v >= o.min[k] && v <= o.max[k])) {
          if (o !== carny) splash(o, now);
          else gasp(now, GASP_MS);
          d[0] = -1e9; break;
        }
    if (d[0] < 0) continue;
    const s = DROP_SIZE * (0.6 + hash(d[0]) * 0.8);
    E.draw(MESH.mesh_cube, new DOMMatrix().translate(...p).scale(s, s, s), hash(d[0] + 5) < 0.15 ? 1 : 10);
  }
};

let aim;
const pointer = e => {
  const r = canvas.getBoundingClientRect();
  return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
};
const setAim = e => {
  const [x, y] = pointer(e);
  aim = [x * hudW, y * hudH];
  if (held) {
    GUN_ROT[0] = clamp(grab[2] + (y - grab[1]) * GUN_TILT, GUN_TILT / 2);
    GUN_ROT[1] = clamp(grab[3] + (grab[0] - x) * GUN_SWING, GUN_SWING / 2);
  }
};
canvas.addEventListener('pointermove', setAim);
canvas.addEventListener('pointerdown', setAim);
canvas.addEventListener('pointerdown', () => { spraying = held; });
const MALLET_Y = 1, MALLET_H = 1.5, MALLET_REST = -90, MALLET_STRIKE = 90, MALLET_YAW = 30, WHACK_MS = 260;
const MOLE_DOWN = -2, MOLE_UP = -0.5;
const MOLE_MS = 1500, MOLE_RISE = 180, MOLE_ODDS = 0.35;
const WALL_Y = 4.5, WALL_Z = -1.9, X_SIZE = 2, HIT_R = 1.1;
const FIRST = 6, FLASH_MS = 300, FLASH_BEAT = 60;
let holes = [], holeBox, whackAt = -1e9, struck = 1, hits = 0, need, visits = 0, malletParts, xParts, wallMole;
const moles = [], struckAt = [], flashAt = [];
const slotOf = (i, now) => {
  const t = now + hash(i) * MOLE_MS, k = t / MOLE_MS | 0;
  return [k, t - k * MOLE_MS];
};
const up = (i, now) => {
  const [k, into] = slotOf(i, now);
  if (struckAt[i] === k && now - flashAt[i] > FLASH_MS) return 0;
  return hash(i * 13 + k) < MOLE_ODDS ? Math.min(1, into / MOLE_RISE, (MOLE_MS - into) / MOLE_RISE) : 0;
};
const moleColor = (i, now) => {
  const [k] = slotOf(i, now);
  return hash(i * 7 + k) < 1 / 3 ? prize : others[hash(i * 3 + k) * others.length | 0];
};
const paintMole = (parts, c) => parts.forEach((p, j) => MOLE[j].color === 4 && (p.color = c));
canvas.addEventListener('pointerdown', () => {
  if (holes.length) { whackAt = performance.now(); stageAt ||= whackAt; struck = 0; playSfx(whack); }
});
const drawWhack = now => {
  paintMole(wallMole ||= spawnEntity(MOLE, MESH), prize);
  holes.forEach((h, i) => {
    paintMole(moles[i] ||= spawnEntity(MOLE, MESH), moleColor(i, now));
    const flash = now - flashAt[i] < FLASH_MS && (now - flashAt[i]) / FLASH_BEAT & 1;
    drawEntity(E, moles[i], h.place.translate(0, MOLE_DOWN + (MOLE_UP - MOLE_DOWN) * up(i, now), 0), flash ? 1 + EMISSIVE : -1);
    if (i >= need) return;
    const w = new DOMMatrix().translate(h.place.m41, WALL_Y + holeBox[2] - h.place.m43, WALL_Z);
    drawEntity(E, wallMole, w);
    if (hits > i) drawEntity(E, xParts ||= spawnEntity(X, MESH), w.translate(0, 1, 0.8).rotate(90, 0, 0).scale(X_SIZE, 1, X_SIZE), 6 + EMISSIVE);
  });
  malletParts ||= spawnEntity(MALLET, MESH);
  const [a, d] = ray(aim[0] / hudW, aim[1] / hudH);
  const k = (MALLET_Y - a[1]) / d[1];
  const px = clamp(a[0] + d[0] * k - holeBox[0], holeBox[1]) + holeBox[0];
  const pz = clamp(a[2] + d[2] * k - holeBox[2], holeBox[3]) + holeBox[2];
  const off = o => Math.hypot(o.place.m41 - px, o.place.m43 - pz);
  const hole = holes.reduce((b, o) => off(o) < off(b) ? o : b);
  const age = (now - whackAt) / WHACK_MS;
  if (!struck && age >= 0.5) {
    struck = 1;
    const i = holes.indexOf(hole);
    if (up(i, now) > 0.5 && off(hole) < HIT_R && struckAt[i] !== slotOf(i, now)[0]) {
      if (moleColor(i, now) === prize) {
        struckAt[i] = slotOf(i, now)[0]; flashAt[i] = now; playSfx(SOUNDS[TAGS.target[0]]);
        if (++hits >= need) winStage();
      } else miss();
    }
  }
  const s = MALLET_REST + (age < 1 ? (MALLET_STRIKE - MALLET_REST) * Math.sin(age * Math.PI) : 0);
  const m = new DOMMatrix().translate(px, MALLET_Y + MALLET_H, pz)
    .rotate(0, 180 + MALLET_YAW, 0).rotate(s, 0, 0).rotate(0, 0, 90);
  drawEntity(E, malletParts, m);
};
addEventListener('pointerup', () => { spraying = 0; });
const drawAim = () =>
  hudRect(aim[0] - 8, aim[1] - 8, 16, 16, -1, { tile: { u: 1, v: 1 } });

const TAGS = {
  bonus: [0, 0],
  enemy: [1, 4],
  target: [0, 0],
  player: 0,
};

const spawned = new Map();

const PALETTES = [
  '',
  '',
];
const mood = p => p ? buildPalette(p.match(/../g).map(h => parseInt(h, 16)), model.shades) : model.palette;
const STAGES = [COLOR_CHOOSER, STAGE_1, STAGE_2];
let stageIndex = 0, lastStage = 0, objects = [], radius = 10, swipe = 0, nextStage = -1,
  palette = model.palette;

const findObject = blueprint => objects.find(o => o.e === blueprint);

const loadPlacements = stage => {
  wheelHit = 0;
  wheelColor = -1;
  wheelSpin = stopSfx(wheelSpin);
  bounceSpin = stopSfx(bounceSpin);
  let targetN = 0;
  others = RAINBOW.filter(c => c !== prize);
  objects = stage.map(it => {
    let parts;
    if (it.e.s) parts = spawnEntity(it.e, MESH);
    else if (!(parts = spawned.get(it.e)))
      spawned.set(it.e, parts = spawnEntity(it.e, MESH));
    const place = trs(it.p, it.r, it.s);
    const t = it.t ?? it.e.t;
    return { e: it.e, parts, place, t, color: it.c ?? -1, n: t === 'target' ? targetN++ : 0, ...worldBounds(parts, place) };
  });
  if (targetN || stage.some(it => it.e === WHACKA)) need = FIRST + visits++;
  objects = objects.filter(o => o.t !== 'target' || o.n < need);
  deal();
  wheel = findObject(COLOR_WHEEL)?.parts;
  if (wheel) for (const p of WEDGES) if (won.includes(p.color)) wheel[COLOR_WHEEL.indexOf(p)].color = 15;
  gun = findObject(WATERGUN);
  timer = findObject(TIMER); stageAt = 0;
  timer?.parts.forEach((p, i) => i && (p.color = prize));
  carny = findObject(CARNY); lost = 0;
  holes = objects.filter(o => o.e === WHACKA); hits = 0; struckAt.length = 0;
  need = Math.min(need, holes.length || need);
  holeBox = [1, 3].flatMap(k => {
    const v = holes.map(o => o.place['m4' + k]);
    return [(Math.min(...v) + Math.max(...v)) / 2, (Math.max(...v) - Math.min(...v)) / 2];
  });
  carnyTo = findObject(MARK)?.place.toFloat64Array();
  objects = objects.filter(o => o.e !== MARK && o.e !== MOLE);
  held = spraying = drops.length = 0;
  GUN_ROT.fill(0);
  sprayLoop = stopSfx(sprayLoop);
  if (import.meta.env.DEV)
    for (const it of stage) {
      const t = it.t ?? it.e.t;
      if (t && !(t in TAGS)) console.warn(`tag '${t}' is not in TAGS -- that placement is scenery`);
      if (it.b) console.warn(`b: ${it.b} on a placement -- palette banks are parked, it draws in the stage palette`);
    }

};

const loadStage = i => {
  if (title === 2) return;
  title = i === WIN_STAGE ? 2 : 0;
  stageIndex = (i + STAGES.length) % STAGES.length;
  if (!title && stageIndex) lastStage = stageIndex;
  E.setPalette(palette = mood(PALETTES[stageIndex]));
  loadPlacements(title ? INTRO : STAGES[stageIndex]);
};
loadPlacements(INTRO);

const goStage = i => {
  if (!swipe && title !== 2) { swipe = performance.now(); nextStage = i; cam.shake = 0; playSfx(SOUNDS[2]); }
};
if (import.meta.env.DEV) {
  window.transitionState = () => [objects.length, !!swipe, nextStage];
  Object.defineProperty(window, 'prize', { get: () => [prize, COLOR_NAMES[prize]] });
}

const cam = makeCamera(GAME_VIEW);
if (import.meta.env.DEV) {
  Object.assign(window, { cam, loadStage });
  Object.defineProperty(window, 'objects', { get: () => objects });
  Object.defineProperty(window, 'projView', { get: () => projView });
}

let DROP = 16, ARC = 44, title = 1;
const STAR_RATE = 60, NOD_MS = 400;
if (import.meta.env.DEV) {
  Object.defineProperty(window, 'ARC', { get: () => ARC, set: v => ARC = v });
  Object.defineProperty(window, 'DROP', { get: () => DROP, set: v => DROP = v });
  const eases = Object.keys(A).filter(k => k === 'linear' || k.startsWith('ease'));
  addEventListener('keydown', e => {
    if (e.key === '[') console.log('arc:', ARC -= 1);
    if (e.key === ']') console.log('arc:', ARC += 1);
    if (e.key === 'c') console.log('ease:', EASE = eases[(eases.indexOf(EASE) + 1) % eases.length]);
    if (e.key === 'p') shot = 1;
  });
}
let shot = 0;
// WebGL buffers are cleared after present (no preserveDrawingBuffer), so the grab
// must run at the end of the frame while both canvases still hold this frame.
const saveShot = () => {
  shot = 0;
  const out = document.createElement('canvas');
  out.width = canvas.width; out.height = canvas.height;
  const c = out.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.drawImage(canvas, 0, 0);
  c.drawImage(hudCanvas, 0, 0, out.width, out.height);
  out.toBlob(b => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = `queenie-${new Date().toISOString().replace(/[:.]/g, '-')}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
};
const drawTitle = () => {
  const big = Math.max(2, hudW / 70 | 0), small = Math.max(1, big >> 1);
  const line = (txt, s, b, y, r) => {
    const x = (hudW - (txt.length * 8 - 1) * s) / 2 | 0;
    for (const d of [1, -1]) {
      drawText(txt, x + d, y, s, DEAD, r);
      drawText(txt, x, y + d, s, DEAD, r);
    }
    drawText(txt, x, y, s, b, r);
  };
  const y = DROP * small | 0;
  if (title === 2) line('YOU WIN!', big, YELLOW, y);
  else {
    line("QUEENIE'S", small, YELLOW, y, ARC * small);
    line('PLAYLAND', big, ORANGE, y + 9 * small);
  }
};
const spinDown = () => {
  if (wheelHit || swipe) return;
  playSfx(SOUNDS[TAGS.bonus[0]]);
  stopWheel();
};
addEventListener('keydown', () => {
  if (wheel) spinDown();
});

let dragged = 0;
canvas.addEventListener('pointerup', e => {
  if (!held && !dragged && !swipe && pointer(e).every(n => n >= 0 && n <= 1))
    title ? goStage(0) : wheel ? spinDown() : holes.length || pick(e);
});

if (import.meta.env.DEV && location.search.includes('orbit')) {
  let dragging = false;
  canvas.addEventListener('pointerdown', e => {
    dragging = true; dragged = 0; canvas.setPointerCapture(e.pointerId);
  });
  addEventListener('pointerup', () => { dragging = false; });
  canvas.addEventListener('pointermove', e => {
    if (!dragging) return;
    dragged += Math.abs(e.movementX) + Math.abs(e.movementY);
    cam.yaw += e.movementX * 0.4;
    cam.pitch = Math.max(-89, Math.min(89, cam.pitch + e.movementY * 0.4));
  });
  canvas.addEventListener('wheel', e => {
    cam.dist = Math.max(radius * 0.5, Math.min(radius * 10, cam.dist * (e.deltaY > 0 ? 1.1 : 0.9)));
    e.preventDefault();
  }, { passive: false });
}

let clicks = 0;
const ray = (x, y) => {
  const inv = projView.inverse();
  const nx = x * 2 - 1, ny = 1 - y * 2;
  const un = w => {
    const p = inv.transformPoint(new DOMPoint(nx, ny, w, 1));
    return [p.x / p.w, p.y / p.w, p.z / p.w];
  };
  const a = un(-1), b = un(1);
  return [a, [b[0] - a[0], b[1] - a[1], b[2] - a[2]]];
};
function pick(e) {
  const [x, y] = pointer(e), [a, dir] = ray(x, y);

  let best = Infinity, hit = -1;
  objects.forEach((o, i) => {
    let t0 = 0, t1 = Infinity;

    for (let k = 0; k < 3; k++) {
      const lo = o.min[k];
      const hi = o.max[k];
      if (Math.abs(dir[k]) < 1e-9) {
        if (a[k] < lo || a[k] > hi) t0 = Infinity;
      } else {
        let ta = (lo - a[k]) / dir[k], tb = (hi - a[k]) / dir[k];
        if (ta > tb) [ta, tb] = [tb, ta];
        t0 = Math.max(t0, ta);
        t1 = Math.min(t1, tb);
      }
    }
    if (t0 <= t1 && t0 < best) { best = t0; hit = i; }
  });
  if (hit >= 0 && objects[hit] === gun && !lost) { held = 1; grab = [x, y, ...GUN_ROT]; stageAt ||= performance.now(); return; }
  const act = hit >= 0 && TAGS[objects[hit].t];
  if (act) {
    const o = objects[hit];
    const wrong = o.t === 'target' && o.color !== prize;
    const feedback = wrong ? TAGS.enemy : act;
    playSfx(SOUNDS[feedback[0]]);
    o.bounce = 1;
    cam.shake = feedback[1];
    if (wrong) {
      clicks = Math.max(0, clicks - 1);
    } else if (++clicks % 10 === 0 && stageIndex !== 0) goStage(stageIndex + 1);
  }
}

let PIXEL_SCALE = 4;
let HUD_SCALE = 2;
const hudW = GAME_VIEW.width / 2, hudH = GAME_VIEW.height / 2;
const resize = () => {
  const { width, height } = GAME_VIEW;
  const fit = Math.min(innerWidth / width, innerHeight / height);
  for (const [c, scale] of [[canvas, PIXEL_SCALE], [hudCanvas, HUD_SCALE]]) {
    c.width = Math.ceil(width * fit * devicePixelRatio / scale);
    c.height = Math.ceil(height * fit * devicePixelRatio / scale);
    c.style.width = width * fit + 'px';
    c.style.height = height * fit + 'px';
  }
  aim ??= [hudW / 2, hudH / 2];
};
addEventListener('resize', resize);
resize();
if (import.meta.env.DEV) {
  window.H = H;
  window.setRenderScale = (world, hud) => {
    PIXEL_SCALE = world; HUD_SCALE = hud; resize();
  };
}

const drawSwipe = now => {
  const p = (now - swipe) / 700;
  if (p >= 0.5 && nextStage >= 0) { loadStage(nextStage); nextStage = -1; }
  if (p >= 1) { swipe = 0; return; }
  const lag = (RAINBOW.length - 1) * SKEW, pw = hudW + lag;
  const h = hudH / RAINBOW.length, x0 = pw * (p * 2 - 1);
  RAINBOW.forEach((c, i) => {
    const x = x0 - i * SKEW;
    hudRect(x, i * h, pw, h, c);
    for (let j = 0; j < FRAY; j++) {
      const k = i * FRAY + j, d = hash(k) ** 2 * SPREAD, w = (1 - d / SPREAD) * 5;
      hudRect(x - d, i * h + hash(k + 3) * (h - w), w, w, c);
    }
  });
};

let last = 0, projView = new DOMMatrix();
let dbgFrames = 0, dbgTime = 0, dbgFps = 0, dbgCalls = 0;
requestAnimationFrame(function loop(now) {
  requestAnimationFrame(loop);
  const dt = (now - last) / 1000; last = now;
  cam.update(dt);

  if (import.meta.env.DEV && DEBUG) dbgCalls = E.drawCalls + H.drawCalls;

  projView = perspective(cam.fov, GAME_VIEW.width / GAME_VIEW.height, cam.dist + radius * 3)
    .multiply(cam.view());

  const carnyParts = spawned.get(CARNY);
  if (carnyParts) {
    const laughing = lost && now - lost > SLIDE_MS;
    if (laughing) gasp(now, LAUGH_MS);
    MOUTH_ROT[0] = laughing ? A.tween(now, LAUGH_MS, 0, LAUGH_DEG,
      import.meta.env.DEV ? A[EASE] : A.ease_out_quad, A.cycle, gaspAt)
      : now - gaspAt < 2 * GASP_MS ? A.tween(now, GASP_MS, 0, GASP_DEG, A.ease_out_quad, A.cycle, gaspAt) : 0;
    if (lost && now - lost > SLIDE_MS + LOSE_MS) goStage(0);
    poseState(carnyParts, CARNY, ...(
      laughing ? A.timeline(now, CARNY_CALM, EYES_LAUGH, A.once, lost + SLIDE_MS)
      : now - gaspAt < EYES_SHOT_MS ? A.timeline(now, CARNY_CALM, EYES_SHOT, A.once, gaspAt)
      : A.timeline(now, CARNY_CALM, EYES_BLINK)));
  }

  if (wheel) {
    if (!wheelHit) wheelSpin ||= playSfx(wheel_rotate, 1, 0.75);
    else wheelSpin = stopSfx(wheelSpin);
    WHEEL_ROT[WHEEL_AXIS] = wheelHit
      ? A.tween(now, WHEEL_STOP, wheelFrom, wheelTo, A.ease_out_quad, A.once, wheelHit)
      : -now * WHEEL_RATE;
    if (wheelHit && wheelColor < 0 && now - wheelHit >= WHEEL_STOP) {
      [wheelColor] = topWedge(wheelTo);
      prize = wheelColor;
      wheelChosenAt = now;
      bounceSpin = playSfx(bounce_lite, 1, 1, 2 * WHEEL_PULSE_MS / 1000);
      if (import.meta.env.DEV)
        console.log('wheel:', COLOR_NAMES[prize] || prize, '-> active');
    }
    const swell = wheelColor < 0 ? 1
      : A.tween(now, WHEEL_PULSE_MS, 1, WHEEL_PULSE, A.ease_out_quad, A.cycle);
    for (let i = 0; i < 3; i++) WHEEL_SCALE[i] = WHEEL_BASE[i] * swell;
    poseState(wheel, COLOR_WHEEL, ...A.timeline(now, WHEEL_STATE, BLINK_STEPS));
    wheel[WHEEL_SELECT].color = wheelColor < 0 ? COLOR_WHEEL[WHEEL_SELECT].color
      : ((now - wheelChosenAt) / WHEEL_BEAT | 0) % 2 ? 0 : wheelColor + EMISSIVE;
    if (stageIndex === 0 && wheelColor >= 0 && now - wheelChosenAt >= WHEEL_BLINKS * 2 * WHEEL_BEAT)
      goStage(1 + lastStage % (STAGES.length - 1));
  }

  E.clear();
  if (now - dealAt > DEAL_MS) { dealAt = now; deal(); }
  if (gun) drawGun(now);
  if (holes.length && aim) drawWhack(now);
  for (const o of objects) {
    // The gun flashes green until it is picked up: the one thing on this
    // stage the player has to find first.
    const color = o === gun && !held && !lost && now / 400 & 1 ? 8 + EMISSIVE : o.color;
    if (title && o.e === UNICORN) {
      const state = UNICORN.s[title - 1];
      poseState(o.parts, UNICORN, state);
      const place = o.place.rotate(
        A.tween(now, NOD_MS, -3, 3, A.ease_in_out_sine, A.cycle), 0, 0);
      drawEntity(E, o.parts, place, o.color, state[0]);
      continue;
    }
    o.bounce = Math.max(0, (o.bounce || 0) - dt * 3.5);
    const s = 1 + 0.25 * Math.sin(o.bounce * Math.PI);
    const spin = hash(o.place.m41 + o.place.m42) < 0.5 ? now / STAR_RATE : -now / STAR_RATE;
    const place = o === carny && lost && carnyTo
      ? DOMMatrix.fromFloat64Array(o.place.toFloat64Array().map((v, i) =>
          A.tween(now, SLIDE_MS, v, carnyTo[i], A.ease_in_out_quad, A.once, lost)))
      : title && o.e === STAR ? o.place.rotate(0, spin, 0)
      : o.place.scale(s, s, s);
    drawEntity(E, o.parts, place, color);
  }

  E.flush(projView);

  H.gl.clearColor(0, 0, 0, 0);
  H.clear();
  if (title) drawTitle();
  if (timer) drawTimer(now);
  if (!held) drawAim();

  if (import.meta.env.DEV && DEBUG) {
    dbgFrames++;
    if ((dbgTime += dt) >= 0.5) {
      dbgFps = Math.round(dbgFrames / dbgTime);
      dbgFrames = dbgTime = 0;
    }
    [`${dbgFps} FPS`, `${dbgCalls} DC`].forEach((t, i) =>
      drawText(t, hudW - 1 - (t.length * 8 - 1), 2 + i * 9));
  }

  if (swipe) drawSwipe(now);
  H.setPalette(swipe ? model.palette : palette);

  H.flush(new DOMMatrix().translate(-1, 1, 0)
    .scale(2 / hudW, -2 / hudH, 1)
    .translate(cam.jx, cam.jy, 0));
  if (import.meta.env.DEV && shot) saveShot();
});

if (import.meta.env.DEV && new URLSearchParams(location.search).has('win')) loadStage(WIN_STAGE);
