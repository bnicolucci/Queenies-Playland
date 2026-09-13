// Tweens: read an animated number straight out of the clock
// =========================================================
// One tween. Everything else here is a one-line function you HAND to it, and
// there are two kinds, working on the two different halves of the job:
//
//   an EASE shapes the VALUE  — 0..1 -> 0..1, the shape of the motion
//   a MODE shapes the TIME    — raw progress -> 0..1, what happens at the ends
//
// So `cycle` is not a second tween, it is the mode argument: the same call
// that plays once plays forever with one word changed. Every helper is a pure
// function of `now`, so no motion keeps state between frames and none can
// drift out of sync with the frame it is drawn in.
//
// Unused easings and modes cost ZERO shipped bytes (they tree-shake), so add
// the curve a motion wants rather than bending an existing one.

export const mix = (a, b, t) => a + (b - a) * t;

// --- easings: 0..1 -> 0..1 -------------------------------------------------
// The standard families, gentlest first: each `in` starts slow, each `out`
// ends slow, each `in_out` does both. Same shape all the way down, only
// sharper -- quad barely leans, quint snaps. Written in their algebraically
// simplified form, which is shorter to read and gives BIT-identical results to
// the textbook spelling (checked: zero deviation over 10,001 samples each).
// Note `ease_in_sine(1)` is 0.9999999999999999 -- Math.cos(PI/2) is 6e-17, not
// 0 -- which is true of the textbook spelling too and matters to nothing.
export const linear = t => t;

export const ease_in_quad = t => t * t;
export const ease_out_quad = t => 1 - (1 - t) ** 2;
export const ease_in_out_quad = t => t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) ** 2;

export const ease_in_cubic = t => t ** 3;
export const ease_out_cubic = t => 1 - (1 - t) ** 3;
export const ease_in_out_cubic = t => t < 0.5 ? 4 * t ** 3 : 1 - 4 * (1 - t) ** 3;

export const ease_in_quart = t => t ** 4;
export const ease_out_quart = t => 1 - (1 - t) ** 4;
export const ease_in_out_quart = t => t < 0.5 ? 8 * t ** 4 : 1 - 8 * (1 - t) ** 4;

export const ease_in_quint = t => t ** 5;
export const ease_out_quint = t => 1 - (1 - t) ** 5;
export const ease_in_out_quint = t => t < 0.5 ? 16 * t ** 5 : 1 - 16 * (1 - t) ** 5;

export const ease_in_sine = t => 1 - Math.cos(t * Math.PI / 2);
export const ease_out_sine = t => Math.sin(t * Math.PI / 2);
export const ease_in_out_sine = t => (1 - Math.cos(t * Math.PI)) / 2;

// Circ is the hardest of the lot: it leaves and arrives at a vertical tangent.
export const ease_in_circ = t => 1 - Math.sqrt(1 - t * t);
export const ease_out_circ = t => Math.sqrt(1 - (t - 1) ** 2);
export const ease_in_out_circ = t => t < 0.5
  ? (1 - Math.sqrt(1 - 4 * t * t)) / 2
  : (1 + Math.sqrt(1 - 4 * (1 - t) ** 2)) / 2;

// The one curve here that leaves 0..1: it overshoots to 1.10 and settles back,
// which is what makes a motion read as alive rather than merely fast.
export const ease_out_back = t => 1 + 2.7 * (t - 1) ** 3 + 1.7 * (t - 1) ** 2;

// --- modes: raw progress -> 0..1 -------------------------------------------
// Progress is elapsed/ms, so it runs past 1 when the tween is over (and is
// negative before it starts). A mode says what to do with that.
export const once = p => p < 0 ? 0 : p > 1 ? 1 : p;   // hold `from`, then `to`
export const cycle = p => 1 - Math.abs(1 - p % 2);    // to and back, forever
export const repeat = p => p % 1;                     // to, snap back, again

// `from` -> `to` over `ms`, shaped by `ease`, ended by `mode`. `t0` is when it
// started, defaulting to the page clock's zero -- it is LAST because a looping
// tween never wants it, and a one-shot names its own start time anyway
// (`tween(now, 180, 0, -25, ease_out_back, once, o.hitAt)`). Re-triggering a
// one-shot is assigning a new `t0`; `ms` is always the time from `from` to
// `to`, so a `cycle` takes another `ms` to come back.
export const tween = (now, ms, from, to, ease = linear, mode = once, t0 = 0) =>
  mix(from, to, ease(mode((now - t0) / ms)));

// A POSE timeline: which two states are on screen right now, and how far
// between them -- the three things poseState takes. `steps` is data, one row
// per move: `[to, ms, hold, ease]` goes to state `to` over `ms`, then stays
// there `hold` ms; the next row moves on from there. `from` is where the
// sequence starts (the base pose, or whatever state it left). Same modes as
// tween: `repeat` loops it (a blink every few seconds), `once` plays it
// through and parks on the last state. Pure function of `now`, like
// everything else here: no cursor, nothing to advance, nothing to drift.
//
//   const [a, b, t] = timeline(now, OPEN, [[SHUT, 80, 60], [OPEN, 120, 2800]]);
//   poseState(parts, BLUEPRINT, a, b, t);
export const timeline = (now, from, steps, mode = repeat, t0 = 0) => {
  let total = 0;
  for (const s of steps) total += s[1] + s[2];
  let t = mode((now - t0) / total) * total;
  for (const [to, ms, hold, ease = linear] of steps) {
    if (t < ms) return [from, to, ease(t / ms)];
    if (t < ms + hold) return [to, to, 0];
    t -= ms + hold;
    from = to;
  }
  return [from, from, 0];
};
