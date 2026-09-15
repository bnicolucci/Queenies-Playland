// Game camera: positioned, aimed, and locked — it never orbits
// ============================================================
// The camera is DATA, not input state. `at` is the point it looks at, `yaw`
// and `pitch` (degrees) are which side it looks from, `dist` is how far back
// the eye sits (0 puts the eye AT `at`, i.e. first person). Positioning and
// aiming is therefore assignment — cam.at = [...], cam.yaw = 60 — and nothing
// in the loop turns those back into an orbit control.
//
// Set `shake` to a few degrees for a hit and update() jitters the AIM while it
// decays. Jitter is per-frame, so the view a frame renders and the view a
// click unprojects through must be the SAME view() result — main.js keeps
// last frame's projView for exactly that reason.

import { orbitView } from './engine.js';

// `o` supplies the placement — yaw, pitch and dist have no sensible default,
// so the caller always names them and there is nothing here to shadow.
export const makeCamera = o => ({
  at: [0, 0, 0],
  shake: 0,       // degrees of aim jitter, decays to nothing
  jx: 0, jy: 0,
  ...o,

  update(dt) {
    // Shaking the AIM instead of the position keeps the subject on screen and
    // costs two numbers instead of a vector. 0.01**dt = 99% gone in a second,
    // and SNAPPED to zero under a tenth of a degree (less than a pixel at this
    // camera): a multiplicative decay never gets there on its own, and any
    // jitter at all, however far below a pixel, flips the floor in drawTimer's
    // screen position -- so the timer digits vibrated by a pixel for the rest
    // of the stage after its first shake.
    this.shake = this.shake > 0.1 ? this.shake * 0.01 ** dt : 0;
    this.jx = (Math.random() - 0.5) * this.shake;
    this.jy = (Math.random() - 0.5) * this.shake;
  },

  // orbitView is just "look at a target from yaw/pitch/dist" — with the input
  // handlers gone it IS a fixed camera, so a locked camera needs no new math.
  view() {
    return orbitView(this.yaw + this.jx, this.pitch + this.jy, this.dist, this.at);
  },
});
