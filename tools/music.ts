// The background song's note data -> PICO-8 SFX hex for src/sfx.js's MUSIC_A /
// MUSIC_B. Edit the notes here, run `bun tools/music.ts`, paste the strings.
// Note data -> PICO-8 SFX hex. null = rest. Each channel is 32 eighth notes.
const SPEED = 12;
const enc = (notes: (number | null)[], wave: number, vol: number, fx: number) => {
    if (notes.length !== 32) throw Error('need 32 notes, got ' + notes.length);
    const hex = (n: number, w: number) => n.toString(16).padStart(w, '0');
    return '00' + hex(SPEED, 2) + '0000' + notes.map(n => n == null ? '00000' : hex(n, 2) + wave + vol + fx).join('');
};
const _ = null;
// melody (C5 = 36): circus chromatic pickups
const melA = [40,39,40,43, 40,40,36,_, 41,40,41,45, 43,43,38,_, 38,37,38,41, 47,47,43,_, 40,43,48,43, 48,48,_,_];
const melB = [45,44,45,48, 43,40,36,40, 41,40,38,35, 36,40,43,43, 45,45,41,45, 43,43,40,36, 38,43,41,38, 36,36,_,_];
// oom-pah bass (C3 = 12): oom, pah, oom, pah
const C = [12,16,7,16], G7 = [7,17,14,17], F = [5,21,12,21];
const bassA = [C,C,G7,G7,G7,G7,C,C].flat();
const bassB = [F,C,G7,C,F,C,G7,C].flat();
const out = {
    melA: enc(melA, 5, 5, 0), bassA: enc(bassA, 1, 4, 5),
    melB: enc(melB, 5, 5, 0), bassB: enc(bassB, 1, 4, 5),
};
for (const [k, v] of Object.entries(out)) console.log(k, v.length, v);
