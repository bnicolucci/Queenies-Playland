// Entity blueprints: compose instanced primitives into game objects
// =================================================================
// Ported in spirit from the old repo's entity system, rebuilt on the
// instanced engine. A blueprint is an array of parts:
//   { mesh: 'mesh_x', pos, rot (degrees), scale, color?, uv?, parent? }
// A part with NO `mesh` is a PIVOT: it draws nothing and has no bounds, it
// just sits in the parent chain so its children turn and scale about IT
// rather than about their own origins (a jaw hinge, a shoulder). Blender
// calls the same thing an Empty. No type field -- the absence of `mesh` is
// the marker, so there is nothing that can disagree with itself.
// `uv` is the engine's per-instance retile option (tile/rect/repeat).
// `parent` is the index of an earlier part; the child's transform composes on
// top of it (including its scale — author child sizes in parent space).
// Rotation order is DOMMatrix.rotate's (rotX applied last).
//
// spawnEntity bakes every part's local matrix ONCE. Drawing then just queues
// world*local per part, so parts batch with every other instance of the same
// meshes — an entity adds zero draw calls of its own.

import { mix } from './anim.js';

// THE definition of translate -> rotate -> scale, in one place. Blueprint
// parts (partMatrix below), stage PLACEMENTS in main.js and the dev-only
// Entity editor's gizmo maths all compose through it, so a correction to the
// order cannot leave one of them silently disagreeing with the others.
export const trs = (pos = [0, 0, 0], rot = [0, 0, 0], scale = [1, 1, 1]) =>
  new DOMMatrix().translate(...pos).rotate(...rot).scale(...scale);

// A blueprint part's own fields in that convention. The Entity editor passes
// only `rot` to get the pure rotation basis.
export const partMatrix = p => trs(p.pos, p.rot, p.scale);

// A part's ABSOLUTE local matrix: its TRS, composed onto its parent's
// already-baked local. A state can supply `local`; both bakers still share the
// parent composition.
const bakeLocal = (parts, p, local = partMatrix(p)) =>
  p.parent == null ? local : parts[p.parent].local.multiply(local);

export function spawnEntity(blueprint, meshByName) {
  const parts = [];
  for (const p of blueprint) {
    const mesh = meshByName[p.mesh];
    // A blueprint naming a mesh model.txt doesn't have used to sail through as
    // `mesh: undefined` and blow up frames later, in whoever read mesh.min /
    // mesh.vao. Say it here, where the name is. DEV-only, so it ships nothing.
    if (import.meta.env.DEV && p.mesh && !mesh)
      throw new Error(`spawnEntity: part ${parts.length} names '${p.mesh}', which model.txt has no mesh for. It has: ${Object.keys(meshByName).join(', ')}`);
    parts.push({ mesh, color: p.color ?? -1, uv: p.uv, local: bakeLocal(parts, p) });
  }
  return parts;
}

// A state is [visibility mask, sparse overrides]. Each override is
// [part index, pos, rot, scale], with 0 standing for an unchanged vector.
// Missing fields inherit the blueprint. Keeping the result as one TRS lets any
// non-uniformly scaled part rotate without manufacturing shear.
// The three TRS fields in `trs` order, each falling back to its identity when
// neither the state nor the blueprint names it. An override is
// [partIndex, pos, rot, scale], so field k reads override slot k + 1 -- the one
// place that offset is spelled out. Read with dot access, never `p['pos']`:
// the pack renames these keys (MANGLE_PROPS in vite.js13k.config.ts) and a
// string would go on asking for the old name -- every posed part silently
// snapped to identity the first time this was a table of strings.
const poseFields = p => [p.pos || [0, 0, 0], p.rot || [0, 0, 0], p.scale || [1, 1, 1]];
// Zero's missing indexed properties fall back below without allocating an array.
const poseDelta = (state, part) => state[1].find(d => d[0] === part) || 0;

// Apply one sparse state, or tween it to b when b/t are supplied. Missing
// vectors resolve to identity, and a posed parent carries its whole assembly.
export const poseState = (parts, blueprint, a, b = a, t = 0) => {
  for (let i = 0; i < blueprint.length; i++) {
    const x = poseDelta(a, i), y = poseDelta(b, i), p = blueprint[i];
    const v = poseFields(p).map((base, k) => {
      const from = x[k + 1] || base, to = y[k + 1] || base;
      return from.map((n, j) => mix(n, to[j], t));
    });
    parts[i].local = bakeLocal(parts, p, trs(...v));
  }
};

// Optional color overrides the WHOLE entity (recolored rows, hit flashes);
// <0 keeps each part's own blueprint color.
// Pivots stay in the array rather than being filtered out after the bake (1
// byte cheaper, measured): every local is baked absolute, so a pivot's matrix
// is the only handle an animation has on the point its children turn about --
// the point poseState above re-composes the sub-tree from.
export const drawEntity = (E, parts, world, color = -1, mask = -1) => {
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.mesh && mask >> i & 1)
      E.draw(p.mesh, world.multiply(p.local), color < 0 ? p.color : color, p.uv);
  }
};

// A spawned entity's world AABB: transform each mesh box's axis intervals,
// then union them. The game picks
// clicks against it; the Entity editor frames the preview with it (pass an
// identity `place`) instead of guessing an entity's size from part positions.
export const worldBounds = (parts, place) => {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const part of parts) {
    if (!part.mesh) continue;          // a pivot has no box to contribute
    const m = place.multiply(part.local).toFloat64Array();
    for (let k = 0; k < 3; k++) {
      let lo = m[12 + k], hi = lo;
      for (let j = 0; j < 3; j++) {
        const a = m[j * 4 + k] * part.mesh.min[j], b = m[j * 4 + k] * part.mesh.max[j];
        lo += Math.min(a, b);
        hi += Math.max(a, b);
      }
      min[k] = Math.min(min[k], lo);
      max[k] = Math.max(max[k], hi);
    }
  }
  return { min, max };
};
