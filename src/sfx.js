// PICO-8 SFX synth + WebAudio playback
// ====================================
// Ported from picocad2-js13k's lib/pico8.ts (SFX render) and lib/audio.ts
// (playback), with one-shots and stoppable loops.
//
// An SFX is the 168-char hex string the PICO-8 sound editor exports:
// 4 header digits (mode, speed, loop start, loop end) then 32 notes of
// pitch(2) wave(1) volume(1) effect(1). Speed is 1/120s ticks per note.

export const SR = 44100;

/** PICO-8's eight waveforms, by index: triangle, tilted saw, saw, square,
    pulse, organ, noise, phaser. `t` is the phase 0..1. Slot 6 is a PLACEHOLDER
    keeping the indices aligned: wave 6 is noise, which renderSfx generates as
    a random walk below and never looks up here. */
const WAVES = [
  t => Math.abs(t * 2 - 1) - 1,
  t => 0.5 * (t < 0.9 ? (2 * t) / 0.9 - 1 : (2 * (1 - t)) / 0.1 - 1),
  t => 0.6 * (t < 0.5 ? t : t - 1),
  t => (t < 0.5 ? 0.5 : -0.5),
  t => (t < 0.3 ? 0.5 : -0.5),
  t => (t < 0.5 ? 3 - Math.abs(24 * t - 6) : 1 - Math.abs(16 * t - 12)) / 9,
  0,
  (t, c) => {
    const p = Math.abs((((c / 128) % 1) * 2) - 1);
    return (Math.abs(4 * ((t + 0.5 * p) % 1) - 2) - Math.abs(8 * t - 4)) / 6;
  },
];

const hz = n => 65 * 2 ** (n / 12);

/** Render mono samples with headroom trim; playback bakes its fixed volume in. */
export function renderSfx(sfx, gain = 0.7) {
  const h = (at, len) => parseInt(sfx.substr(at, len), 16);
  const speed = h(2, 2);
  const loopStart = h(4, 2);
  const loopEnd = h(6, 2) || 32;
  const noteSecs = speed / 120;
  const len = Math.round(noteSecs * SR);
  const total = Math.max(1, Math.round(loopEnd * noteSecs * SR));
  const data = new Float32Array(total);
  const field = (n, off, len) => h(8 + 5 * n + off, len);

  let write = 0, phase = 0, noise = 0;
  let prevPitch = 24, prevHz = hz(24), prevWave = -1, prevVol = -1, prevFx = -1;

  for (let n = 0; n < loopEnd; n++) {
    const pitch = field(n, 0, 2);
    const freq = hz(pitch);
    const wave = field(n, 2, 1);
    const vol = field(n, 3, 1) / 8;
    const fx = field(n, 4, 1);
    const next = n + 1 >= loopEnd ? loopStart : n + 1;
    const nPitch = field(next, 0, 2);
    const nWave = field(next, 2, 1);
    const nVol = field(next, 3, 1);
    const nFx = field(next, 4, 1);

    // PICO-8 slurs notes tied by pitch or a slide: no attack/release click.
    let atk = 0.02;
    if (fx === 4 || (wave === prevWave && (pitch === prevPitch || fx === 1) && prevVol > 0 && prevFx !== 5)) atk = 0;
    let rel = 0.05;
    if (fx === 5 || (wave === nWave && (pitch === nPitch || nFx === 1) && nVol > 0 && nFx !== 4)) rel = 0;

    for (let i = write; i < write + len && i < total; i++) {
      const u = (i - write) / len;
      let env = 1;
      if (u < atk) env = u / atk;
      else if (u > 1 - rel) env = (1 - u) / rel;

      let f = freq;
      let v = vol;
      if (fx === 1) {
        f = (1 - u) * prevHz + u * freq;
        if (prevVol > 0) v = (1 - u) * prevVol + u * vol;
      }
      if (fx === 2) f *= 1 + 0.02 * Math.sin(7.5 * u);
      if (fx === 3) f *= 1 - u;
      if (fx === 4) v *= u;
      if (fx === 5) v *= 1 - u;
      if (fx >= 6) {
        const span = (speed <= 8 ? 32 : 16) / (fx === 6 ? 4 : 8);
        f = hz(field((n & ~3) | (3 & ((span * u) | 0)), 0, 2));
      }
      phase += f / SR;
      if (wave === 6) {
        noise = (noise + 0.02 * (2 * Math.random() - 1)) / 1.02;
        data[i] = gain * v * env * 10 * noise;
      } else {
        data[i] = gain * v * env * WAVES[wave](phase % 1, phase);
      }
    }
    write += len;
    prevPitch = pitch;
    prevHz = freq;
    prevWave = wave;
    prevVol = vol;      // normalised, like `vol` — only ever tested > 0 or blended
    prevFx = fx;
  }
  return data;
}

// --- playback --------------------------------------------------------------
// The AudioContext can only start after a user gesture, so it is created
// lazily inside playSfx; frame-driven loops remain suspended until a gesture.

let ctx;
const buffers = new Map();

// Stop an optional loop and return its idle value for the caller's handle.
export const stopSfx = src => {
  if (src) src.stop();
  return 0;
};

// `gain` is a multiplier on the fixed game volume (1 = unchanged), baked into
// the render like the headroom trim already was -- so it costs one multiply,
// not a GainNode. `seconds`, if given, retimes the WHOLE buffer to last that
// long via playbackRate -- for a loop that needs to land on a beat elsewhere
// (pitch shifts along with it, which reads as chiptune rather than a glitch).
export function playSfx(sfx, loop, gain = 1, seconds) {
  ctx ??= new AudioContext();
  let buf = buffers.get(sfx);
  if (!buf) {
    const data = renderSfx(sfx, 0.35 * gain);   // 0.7 headroom * fixed 0.5 game volume
    buf = ctx.createBuffer(1, data.length, SR);
    buf.getChannelData(0).set(data);
    buffers.set(sfx, buf);
  }
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.loop = !!loop;
  if (seconds) src.playbackRate.value = buf.duration / seconds;
  src.connect(ctx.destination);
  src.start();
  return src;   // callers of a loop need the handle back to .stop() it
}

// Chime and miss feedback from the prior project's bank, plus the wipe sound.
export const SOUNDS = [
  '000400002474526745297452e7453074532745357453a7452400526005290052e0053000532005350053a00500000000000000000000000000000000000000000000000000000000000000000000000000000000', // sfx14 chime
  '000400002763022630206201b6201661015610116100d6100b6100761005610036100261002610026100261001610016100161501600016000160001600000000000000000000000000000000000000000000000', // sfx19 whoosh
  '00040015185211a5211b5311d5311e531205312154123541245412655128551295512b5512c5612e5612f55131541325313452135511375110000000000000000000000000000000000000000000000000000000', // rising whoosh
];

export const wheel_rotate = '0002000002215006200341500630052150063008415006300b215006400d415006401022500640124250065011225006400f425006400d2150064009415006300621500630054150063003215006300341500620';   // the prize wheel's hum, looped while it spins
export const bounce_lite = '00010000197770c700197770c7001c7670c7001c7570c7001e7570c700217470c700217370c700237370c700237270c700257170c700287170c7000c7000c700135000c600135000c600135050c605135050c605';    // the prize's swell, looped on the wheel's pulse
export const water_spray = '0005000011574160741357418074155641a064165641b054185541d0541a7541f5441b044217441d544220441f744245342103426734220242772424014297140070400704007040070400704007040070400704';
export const whack = '00020000071540f163163730b22332643216331c6231861315613136130e6130a61304600000000000000000000000b1010710105101031010110100000000000000000000000000000000000000000000000000';
export const carnysound = '00010000287770c700257770c700257670c700237570c700237570c700217470c700217370c7001e7370c7001c7270c7001c7170c70019717127050c700127050070000700007000070000700007000070000700';  // 0.27s, 11 notes, wave 7