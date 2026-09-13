// Tiny WebGL2 engine for the picoCAD shaders (shaders/model.vert + model.frag)
// ============================================================================
// w.js-inspired but built around this shader pair's attribute/uniform layout.
// One program, two textures (index + palette), interleaved VAOs, DOMMatrix math.
// INSTANCED (ported from the old repo's renderer.js): draw() queues copies —
// world matrix + color override in divisor-1 attributes — and flush() issues
// one drawArraysInstanced per distinct mesh, so draw calls scale with mesh
// VARIETY, not object count.

import { STRIDE } from './pico.js';

// Atlas tile size in texels that `draw`'s `uv.tile` assumes when a part does
// not override it. Exported so the Entity editor's tile picker snaps to the
// same grid the shader samples — two copies of this number means the picker
// highlights one tile while the renderer draws another.
export const DEFAULT_TILE_SIZE = 16;

export function createEngine(canvas, vertSrc, fragSrc) {
  // antialias off: MSAA would blend colors along polygon edges inside the
  // low-res buffer, softening the chunky-pixel look
  const gl = canvas.getContext('webgl2', { antialias: false });
  if (!gl) throw new Error('WebGL2 not supported');

  // --- program ---
  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (import.meta.env.DEV && !gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, vertSrc));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fragSrc));
  gl.linkProgram(prog);
  if (import.meta.env.DEV && !gl.getProgramParameter(prog, gl.LINK_STATUS))
    throw new Error(gl.getProgramInfoLog(prog));
  gl.useProgram(prog);

  const u = name => gl.getUniformLocation(prog, name);
  // These are program state, not mesh/frame state. Resolve them once.
  const projLoc = u('u_projView'), uvLoc = u('u_uvSrc');

  // Floats per instance: mat4 world (16) + uvRect (4) + params (4):
  // color override, uv mode (0 off / 1 repeat / 2 retile), repeatU, repeatV
  const IFLOATS = 24;
  const meshes = [];   // every mesh ever built — flush() walks this

  gl.enable(gl.DEPTH_TEST);
  // Culling is disabled by default. Keep it that way: picoCAD faces are
  // double-sided and the fragment shader flips their back-face normals.

  // --- textures ---
  let texW, texH, bg;
  const makeTex = (unit, internal, format, w, h, data) => {
    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  };

  const E = {
    gl,

    // Upload the parsed picoCAD model's textures and static uniforms
    setModel(model) {
      const t = model.texture;
      texW = t.width;    // uv.tile rects are computed against
      texH = t.height;   // the model texture's dimensions
      bg = model.bg;     // the background SLOT — see setPalette
      makeTex(0, gl.R8, gl.RED, texW, texH, t.pixels);
      makeTex(1, gl.RGB8, gl.RGB, 16, 3, null);
      // Samplers default to texture unit 0, so only the palette needs setting.
      gl.uniform1i(u('u_paletteTexture'), 1);
      gl.uniform1f(u('u_transparentColor'), model.transparentColor);
      E.setPalette(model.palette);
    },

    // Recolour everything: the model's texture holds palette INDICES, so a
    // whole new mood is 144 bytes overwritten in the 16x3 palette texture
    // (build them with pico.js's buildPalette). The clear colour follows the
    // swap out of the new row 0, since the model's background `bg` is a SLOT
    // and not a colour — which is also why a swap may never renumber slots:
    // u_transparentColor is an index the shader discards, so moving it changes
    // which pixels are HOLES rather than what colour anything is.
    setPalette(bytes) {
      // setModel leaves palette unit 1 active and both textures bound.
      // Drawing only changes VAOs/buffers; there is no texture rebinding.
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 16, 3, gl.RGB, gl.UNSIGNED_BYTE, bytes);
      gl.clearColor(bytes[bg * 3] / 255, bytes[bg * 3 + 1] / 255, bytes[bg * 3 + 2] / 255, 1);
    },

    // Build a VAO from parser output {data}, plus the AABB that
    // entity.js's worldBounds unions for click-picking and stage framing
    // (position = floats 0-2 of each vertex).
    mesh({ data }) {
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      let u0 = Infinity, v0 = Infinity, u1 = -Infinity, v1 = -Infinity;
      for (let i = 0; i < data.length; i += STRIDE) {
        for (let k = 0; k < 3; k++) {
          if (data[i + k] < min[k]) min[k] = data[i + k];
          if (data[i + k] > max[k]) max[k] = data[i + k];
        }
        // uv at floats 6,7 — the mesh's own patch of atlas, the source rect
        // a per-instance retile maps out of
        if (data[i + 6] < u0) u0 = data[i + 6];
        if (data[i + 6] > u1) u1 = data[i + 6];
        if (data[i + 7] < v0) v0 = data[i + 7];
        if (data[i + 7] > v1) v1 = data[i + 7];
      }
      const vao = gl.createVertexArray();
      gl.bindVertexArray(vao);
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      // a_position(3) a_normal(3) a_texCoord(2) a_colorIndex(1) a_faceFlags(1)
      // Locations are explicit in model.vert; no name lookups per mesh.
      let offset = 0;
      for (const [loc, size] of [3, 3, 2, 1, 1].entries()) {
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, size, gl.FLOAT, false, STRIDE * 4, offset * 4);
        offset += size;
      }

      // Per-instance attributes (divisor 1): a_world mat4 spans 4 consecutive
      // locations, then a_uvRect and a_params — 6 vec4 rows, IFLOATS floats.
      const ivbo = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, ivbo);
      for (let loc = 5; loc < 11; loc++) {
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 4, gl.FLOAT, false, IFLOATS * 4, (loc - 5) * 16);
        gl.vertexAttribDivisor(loc, 1);
      }

      const mesh = { vao, ivbo, count: data.length / STRIDE, min, max, inst: [],
        uvSrc: u0 > u1 ? [0, 0, 1, 1] : [u0, v0, u1 - u0, v1 - v0] };
      meshes.push(mesh);
      return mesh;
    },

    clear() {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (import.meta.env.DEV) E.drawCalls = 0;
    },

    // Queue one copy of a mesh: world matrix (DOMMatrix), optional palette
    // color override (0-15; -1 keeps the mesh's own colors), optional uv
    // retile: { tile: { u, v, size=16 } } (1-indexed atlas tile) or
    // { rect: [x, y, w, h] } (texture fractions), each with optional
    // repeatU/repeatV; repeat alone tiles within the mesh's own uv rect.
    // Nothing renders until flush().
    draw(mesh, world, color = -1, uv) {
      // A tile IS a dest rect, just spelled in atlas cells. Having one means
      // mode 2 (remap into it); a bare repeat is mode 1 (tile within the
      // mesh's own rect); no `uv` at all is mode 0.
      const t = uv?.tile, s = t?.size ?? DEFAULT_TILE_SIZE;
      const dest = t
        ? [(t.u - 1) * s / texW, (t.v - 1) * s / texH, s / texW, s / texH]
        : uv?.rect;
      mesh.inst.push(...world.toFloat32Array(), ...(dest ?? [0, 0, 0, 0]),
        color, dest ? 2 : uv ? 1 : 0, uv?.repeatU ?? 1, uv?.repeatV ?? 1);
    },

    // Render everything queued since the last flush: ONE instanced call per
    // distinct mesh, however many copies were drawn. `E.drawCalls` (dev
    // console) reports the per-frame total.
    flush(projView) {
      gl.uniformMatrix4fv(projLoc, false, projView.toFloat32Array());
      for (const m of meshes) {
        if (!m.inst.length) continue;
        gl.uniform4fv(uvLoc, m.uvSrc);
        gl.bindVertexArray(m.vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, m.ivbo);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(m.inst), gl.DYNAMIC_DRAW);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, m.count, m.inst.length / IFLOATS);
        m.inst.length = 0;
        if (import.meta.env.DEV) E.drawCalls++;
      }
    },
  };
  if (import.meta.env.DEV) E.drawCalls = 0;
  return E;
}

// --- DOMMatrix camera helpers (the w.js trick: no math library) ------------

export const perspective = (fov, aspect, far) => {
  const f = 1 / Math.tan(fov * Math.PI / 360);
  const d = 0.1 - far;   // near - far; 0.1 is the near plane, named once here
  return new DOMMatrix([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (0.1 + far) / d, -1,
    0, 0, 0.2 * far / d, 0,
  ]);
};

// Orbit view matrix: distance out, pitch down, yaw around target
export const orbitView = (yaw, pitch, dist, target) =>
  new DOMMatrix()
    .translate(0, 0, -dist)
    .rotate(pitch, 0, 0)
    .rotate(0, yaw, 0)
    .translate(-target[0], -target[1], -target[2]);
