// picoCAD2 model.txt parser
// =========================
// Pure JS, no DOM/GL dependencies (testable in node/bun).
// Parses the JSON saved by picoCAD 2 into flat GPU-ready data matching
// the attribute layout of shaders/model.vert:
//   a_position(3) a_normal(3) a_texCoord(2) a_colorIndex(1) a_faceFlags(1)
// => interleaved stride of 10 floats per vertex.

export const STRIDE = 10;

// Column-major 4x4 multiply — the only matrix math the parser needs, and the
// only piece of the old project's math.js that ever had a caller. Kept here so
// the parser stays dependency-free and testable headless (no DOM, no GL).
const matMul = (a, b) => {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++)
    for (let row = 0; row < 4; row++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = sum;
    }
  return out;
};

// picoCAD node rotations are in RADIANS, applied per axis. (Motion deltas in
// the same file ARE turns -- do not let that leak back in here; see CLAUDE.md.)
const matTRS = ({ pos, rot, scale }) => {
  const [cx, sx, cy, sy, cz, sz] =
    [Math.cos(rot.x), Math.sin(rot.x), Math.cos(rot.y),
     Math.sin(rot.y), Math.cos(rot.z), Math.sin(rot.z)];
  // T * Rz * Ry * Rx * S directly: no five temporary matrices or four
  // general 4x4 multiplies for every node in the imported graph.
  return new Float32Array([
    cz*cy*scale.x, sz*cy*scale.x, -sy*scale.x, 0,
    (cz*sy*sx-sz*cx)*scale.y, (sz*sy*sx+cz*cx)*scale.y, cy*sx*scale.y, 0,
    (cz*sy*cx+sz*sx)*scale.z, (sz*sy*cx-cz*sx)*scale.z, cy*cx*scale.z, 0,
    pos.x, pos.y, pos.z, 1,
  ]);
};

const xfPoint = (m, x, y, z) => [
  m[0]*x + m[4]*y + m[8]*z + m[12],
  m[1]*x + m[5]*y + m[9]*z + m[13],
  m[2]*x + m[6]*y + m[10]*z + m[14],
];

// The one texture width in the system. picoCAD2 atlases are 128x128, the
// parser derives the height from the pixel count against this, and the compact
// encoder imports it to write UVs in TEXEL units — so all three agree by
// construction rather than by three copies of the literal.
export const TEX_W = 128;

// --- compact format --------------------------------------------------------
// The js13k build re-encodes models as `pc2!` + positional tuples (see
// tools/vite/picocad_compact.ts). Decoding reconstructs the raw JSON shape so
// the rest of the parser has a single format to care about.

const COMPACT_PREFIX = 'pc2!';

const dvec = (v, def) => v ? { x: v[0], y: v[1], z: v[2] } : { x: def, y: def, z: def };

const dnode = n => ({
  visible: !!n[0],
  transform: { pos: dvec(n[1], 0), rot: dvec(n[2], 0), scale: dvec(n[3], 1) },
  mesh: n[4] ? {
    vertices: n[4][0],
    faces: n[4][1].map(f => ({
      // UVs ride as TEXELS and come back as texture fractions here, so the
      // parser below has one definition of what a uv is (the same split the
      // palette hex uses). Texels are what the authored values actually ARE:
      // nearly every one lands on a texel boundary, and rounding the fraction
      // instead used to drop some of them a whole texel — see the encoder.
      vertex_ids: f[0], uvs: f[1].map(u => u / TEX_W), color: f[2],
      noshade: !!(f[3] & 1), notex: !!(f[3] & 2),
    })),
  } : undefined,
  children: n[5].map(dnode),
  // Mesh-node names survive compaction (they become each object's `name`).
  name: n[6],
});

function decodeCompact(text) {
  const [t, graph] = JSON.parse(text.slice(COMPACT_PREFIX.length));
  return {
    texture: {
      // Colours ride as 96 hex chars (the shape a mood palette is written
      // in); back to 0..1 here so the parser has ONE definition of how a
      // colour becomes a byte. The /255 * 255 round trip is exact under the
      // Math.round there, and picoCAD's 14-digit decimals were the
      // highest-entropy run in the file: -57 zip bytes.
      pixels: t[0], colors: t[1].match(/../g).map(h => parseInt(h, 16) / 255), transparent_color: t[2],
      shade_pal_1: t[3], shade_pal_2: t[4], background_color: t[5],
    },
    graph: dnode(graph),
  };
}

// --- parser ----------------------------------------------------------------

/**
 * Build the 16x3 palette the fragment shader samples: row 0 = the 16 base
 * colours, rows 1/2 = the two shade tables resolved to colours. `colors` is
 * 48 bytes (16 flat r,g,b triples — the shape a hex palette string parses to
 * in one `match`); `shades` holds the two shade tables, which are
 * indices INTO them (& 15: shade palettes may use PICO-8 extended color
 * numbers 16-31, which map to their standard sibling since only 16 RGBs
 * are stored).
 *
 * Exported because this is the whole of a palette SWAP: every pixel in the
 * model is an INDEX, so 16 fresh RGBs recolour the world — and the shade rows
 * re-derive for free, since the shade tables point at the new colours too.
 */
export function buildPalette(colors, shades) {
  const bytes = new Uint8Array(16 * 3 * 3);
  for (let r = 0; r < 3; r++)
    for (let i = 0; i < 16; i++) {
      const c = (r ? shades[r - 1][i] & 15 : i) * 3;
      bytes.set(colors.slice(c, c + 3), (r * 16 + i) * 3);
    }
  return bytes;
}

export function parsePicoCAD(json) {
  json = json.startsWith(COMPACT_PREFIX) ? decodeCompact(json) : JSON.parse(json);
  const tex = json.texture;

  // Index texture: one hex char per pixel -> palette index 0-15
  const pixels = Uint8Array.from(tex.pixels, c => parseInt(c, 16));

  const colors = tex.colors.flat().map(v => Math.round(v * 255));
  const shades = [tex.shade_pal_1, tex.shade_pal_2];

  // Walk the scene graph, baking each node's accumulated transform into
  // its mesh's vertices. Each mesh becomes one drawable object.
  const objects = [];
  const walk = (node, parent) => {
    if (!node.visible) return;
    const m = matMul(parent, matTRS(node.transform));
    if (node.mesh) objects.push(buildMesh(node.mesh, m, node.name));
    for (const c of node.children) walk(c, m);
  };
  // Root mirrors X: picoCAD2 model space is mirrored vs. WebGL's, so without
  // a reflection the whole scene renders flipped (left/right swapped). X
  // rather than Z — either fixes the handedness, but they differ by a 180°
  // turn of every part (X matches picocad2-js13k's convention).
  walk(json.graph, [-1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);

  return {
    texture: { width: TEX_W, height: pixels.length / TEX_W, pixels },
    palette: buildPalette(colors, shades),
    shades,                    // kept: a palette swap resolves through these
    // The transparent colour and the background are palette SLOTS, not
    // colours — a swap recolours the slot, it must never renumber it.
    transparentColor: tex.transparent_color,
    bg: tex.background_color,
    objects,
  };
}

function buildMesh(mesh, m, name) {
  const vs = mesh.vertices;
  const out = [];
  for (const face of mesh.faces) {
    const ids = face.vertex_ids;               // 1-indexed
    const flags = (face.noshade ? 1 : 0) | (face.notex ? 2 : 0);

    // World-space corners
    const pts = ids.map(id =>
      xfPoint(m, vs[(id-1)*3], vs[(id-1)*3+1], vs[(id-1)*3+2]));

    // Flat face normal (Newell's method, robust for n-gons)
    let nx = 0, ny = 0, nz = 0;
    for (let i = 0; i < pts.length; i++) {
      const [ax, ay, az] = pts[i], [bx, by, bz] = pts[(i+1) % pts.length];
      nx += (ay - by) * (az + bz);
      ny += (az - bz) * (ax + bx);
      nz += (ax - bx) * (ay + by);
    }
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;

    // Fan-triangulate the n-gon
    for (let i = 1; i < pts.length - 1; i++)
      for (const k of [0, i, i + 1])
        out.push(...pts[k], nx, ny, nz,
                 face.uvs[k*2], face.uvs[k*2+1], face.color, flags);
  }
  return { name, data: new Float32Array(out) };
}
