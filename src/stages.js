// Written by the Blender addon's Stage panel (tools/blender/pc2_entity), and
// safe to hand-edit between exports: Export Stage replaces ONE block and leaves
// this header, the other stages and its own import line alone.
// A stage is one level: a flat list of placed entities, loaded on its own.
//   e = the blueprint itself (imported, so unused entities still tree-shake)
//   p / r / s = pos / rot (degrees, DOMMatrix.rotate order) / scale, each
//   omitted when it is the identity.
//   c = a palette colour override for the whole entity.
//   b = the palette BANK this placement resolves its indices through, 0 by
//   default (the world's palette, which follows the stage). A higher bank is a
//   whole second 16-colour scheme, so two placements of the SAME blueprint can
//   wear different colours in one frame -- see the PALETTE_BANKS block in
//   main.js for what each bank holds. The Stage panel round-trips `b`.
// One export per stage, for the same reason entities.js has one per entity:
// a stage nothing imports costs zero packed bytes.
// main.js's loadStage is what reads all this — it bakes p/r/s with entity.js's
// `trs`, the same composition a blueprint part gets.

import { CARNY, COLOR_WHEEL, CUBE, MALLET, MARK, MOLE, RAINBOW, STAR, TARGET_1, TIMER, UNICORN, WATERGUN, WHACKA } from './entities.js';

export const INTRO = [
  { e: UNICORN, p: [0, -1.3, 0], r: [14.8335, -16.8271, -7.6778], s: [3.9835, 3.9835, 3.9835] },
  { e: RAINBOW, p: [0, -0.5534, -5], r: [0, 180, -29.8227], s: [0.7785, 0.7785, 0.7785] },
  { e: RAINBOW, p: [0, -0.5534, -5], r: [180, 0, -29.8228], s: [0.7785, 0.7785, 0.7785] },
  { e: STAR, p: [4.7212, 6.593, -1.1625], r: [-90, 0, -22.8843], c: 5 },
  { e: STAR, p: [-4.871, 9.8895, -1.7438], r: [-90, 0, 30.7212], s: [0.612, 0.612, 0.612], c: 2 },
  { e: STAR, p: [-4.871, -3.6409, 0.642], r: [-90.7409, -8.7905, -21.1042], s: [0.612, 0.612, 0.612], c: 7 },
  { e: STAR, p: [5.2708, 9.1761, -1.618], r: [-89.9517, -10.3737, -31.2538], s: [1.1112, 1.1112, 1.1112], c: 13 },
  { e: STAR, p: [-4.7212, 6.4454, -1.1365], r: [-90, 0, 32.2497], s: [1.9643, 1.9643, 1.9643], c: 10 },
];

export const STAGE_1 = [
  { e: CARNY, p: [2.8976, 1.2778, 8.2354], r: [2.0682, -27.2482, -5.4757], s: [1.3177, 1.3177, 1.3177] },
  { e: TARGET_1, p: [-0.0172, 1.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [-2.0172, 1.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [1.9828, 3.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [-0.0172, 3.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [-2.0172, 3.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [1.9828, 5.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [-0.0172, 5.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [-2.0172, 5.923, 1.3301], c: 1 },
  { e: TARGET_1, p: [1.9828, 1.923, 1.3301], c: 1 },
  { e: CUBE, p: [0, 0.8559, 9.8532], s: [9, 1.7117, 1] },
  { e: CUBE, p: [0, 1.8429, 10.0098], r: [5.0915, 0, 0], s: [8.9659, 0.3276, 1.4043], c: 9 },
  { e: CUBE, p: [5.1579, 1.8413, 3.8932], r: [0, 84, 0], s: [11.1407, 0.3276, 1.277], c: 9 },
  { e: CUBE, p: [-5.1579, 1.8413, 3.8932], r: [0, -84, 0], s: [11.1407, 0.3276, 1.277], c: 9 },
  { e: CUBE, p: [0, 0.8559, 10.3311], r: [0, 180, 0], s: [0.6516, 1.7117, 0.1334], c: 9 },
  { e: CUBE, p: [1.5, 0.8559, 10.3311], r: [0, 180, 0], s: [0.6516, 1.7117, 0.1334], c: 9 },
  { e: CUBE, p: [3, 0.8559, 10.3311], r: [0, 180, 0], s: [0.6516, 1.7117, 0.1334], c: 9 },
  { e: CUBE, p: [-1.5, 0.8559, 10.3311], r: [0, 180, 0], s: [0.6516, 1.7117, 0.1334], c: 9 },
  { e: CUBE, p: [-3, 0.8559, 10.3311], r: [0, 180, 0], s: [0.6516, 1.7117, 0.1334], c: 9 },
  { e: CUBE, p: [-4.9334, 5.9307, 8.4921], r: [0, 5, 0], s: [0.4, 11.8614, 0.4], c: 12 },
  { e: CUBE, p: [4.9939, 5.9307, 8.4909], r: [0, -5, 0], s: [0.4, 11.8614, 0.4], c: 12 },
  { e: CUBE, p: [-5.4091, 5.45, 3.0544], r: [0, 5, 0], s: [0.24, 12.7438, 10.4142], c: 14 },
  { e: CUBE, p: [5.4697, 5.45, 3.0532], r: [0, -5, 0], s: [0.24, 12.7438, 10.4142], c: 14 },
  { e: CUBE, p: [0, 5.6524, -2.3204], s: [12.7975, 11.3047, 0.4], c: 15 },
  { e: RAINBOW, p: [-2.9117, 1.6489, 10.5596], r: [180, 0, -1.17], s: [0.1146, 0.1146, 0.0252] },
  { e: RAINBOW, p: [0.0537, 1.6489, 10.5596], r: [180, 0, 0], s: [0.1146, 0.1146, 0.0252] },
  { e: RAINBOW, p: [2.8733, 1.6927, 10.5596], r: [180, 0, -0.0655], s: [0.1146, 0.1146, 0.0252] },
  { e: WATERGUN, p: [0, 2.4074, 11.2722], r: [0, 180, 0], s: [2.3539, 2.3539, 2.3539] },
  { e: CUBE, p: [0, 0, 3.5841], r: [-2.4768, 0, 0], s: [13.4847, 0.3, 14.7353], c: 13 },
  { e: TIMER, p: [0, 9.7303, -1.9786], r: [90, 0, 0] },
  { e: MARK, p: [0.1453, 1.6372, 8.2001], r: [-4.3504, 0.5787, 7.5623] },
];

export const STAGE_2 = [
  { e: CUBE, p: [0, 0.8559, 9.8532], s: [9, 1.7117, 1], c: 10 },
  { e: CUBE, p: [0, 1.8429, 10.0098], r: [5.0915, 0, 0], s: [8.9659, 0.3276, 1.4043], c: 11 },
  { e: CUBE, p: [5.1579, 1.8413, 3.8932], r: [0, 84, 0], s: [11.1407, 0.3276, 1.277], c: 11 },
  { e: CUBE, p: [-5.1579, 1.8413, 3.8932], r: [0, -84, 0], s: [11.1407, 0.3276, 1.277], c: 11 },
  { e: CUBE, p: [0, 0.8559, 10.3311], r: [0, 180, 0], s: [0.391, 1.7117, 0.1334], c: 11 },
  { e: CUBE, p: [1.5, 0.8559, 10.3311], r: [0, 180, 0], s: [0.391, 1.7117, 0.1334], c: 11 },
  { e: CUBE, p: [3, 0.8559, 10.3311], r: [0, 180, 0], s: [0.391, 1.7117, 0.1334], c: 11 },
  { e: CUBE, p: [-1.5, 0.8559, 10.3311], r: [0, 180, 0], s: [0.391, 1.7117, 0.1334], c: 11 },
  { e: CUBE, p: [-3, 0.8559, 10.3311], r: [0, 180, 0], s: [0.391, 1.7117, 0.1334], c: 11 },
  { e: CUBE, p: [-4.8805, 5.45, 3.0544], r: [0, 5, 0], s: [0.24, 12.7438, 10.4142], c: 11 },
  { e: CUBE, p: [4.9318, 5.45, 3.0532], r: [0, -5, 0], s: [0.24, 12.7438, 10.4142], c: 11 },
  { e: CUBE, p: [0, 5.6524, -2.3204], s: [12.7975, 11.3047, 0.4], c: 15 },
  { e: RAINBOW, p: [4.6227, 10.2375, 3.3869], r: [-43.021, -81.9018, -136.6927], s: [0.4989, 0.4989, 0.2253] },
  { e: RAINBOW, p: [-4.6227, 10.2375, 3.3869], r: [-43.0206, 81.9017, 136.6928], s: [0.4989, 0.4989, 0.2253] },
  { e: CUBE, p: [0, 0, 3.5841], r: [-2.4768, 0, 0], s: [13.4847, 0.3, 14.7353], c: 9 },
  { e: TIMER, p: [0, 9.7303, -1.9786], r: [90, 0, 0] },
  { e: MARK, p: [0.1453, 1.6372, 8.2001], r: [-4.3504, 0.5787, 7.5623] },
  { e: MOLE, p: [0, 0.5, 4.8] },
  { e: WHACKA, p: [0, 1, 4.8] },
  { e: WHACKA, p: [0, 1, 2.8] },
  { e: WHACKA, p: [0, 1, 0.8] },
  { e: WHACKA, p: [-2, 1, 0.8] },
  { e: WHACKA, p: [-2, 1, 2.8] },
  { e: WHACKA, p: [-2, 1, 4.8] },
  { e: WHACKA, p: [2, 1, 4.8] },
  { e: WHACKA, p: [2, 1, 2.8] },
  { e: WHACKA, p: [2, 1, 0.8] },
  { e: MALLET },
];

export const COLOR_CHOOSER = [
  { e: CARNY, p: [2.5008, 0.7023, 9.672], r: [-11.232, -18.5834, 9.9898] },
  { e: COLOR_WHEEL, p: [0, -1.1555, 5.2059], r: [0, 180, 0], s: [4.0782, 4.0782, 4.0782] },
  { e: STAR, p: [0.0102, 3.9534, 3.6108], r: [90, 0, 0] },
];
