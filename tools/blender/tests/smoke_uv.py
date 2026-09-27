"""UV retile: authoring, export, and the viewport preview.

The preview material is checked by walking the node graph the addon actually
built and evaluating it in Python, then comparing against the fragment shader's
retile() over a grid of sample UVs. That catches a wrong constant or a wrong
V-flip, which eyeballing a screenshot would not.
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.out("entities_uv.js")
if os.path.exists(H.out("entities_uv.js")):
    os.remove(H.out("entities_uv.js"))
bpy.ops.pc2.load_primitives()

H.section("[1] mesh_uv_src vs model.txt")
model = json.load(open(H.MODEL, encoding="utf-8"))
raw_uvs = {}


def walk(node):
    if node.get("mesh") and node.get("name"):
        pairs = []
        for face in node["mesh"].get("faces", []):
            uvs = face.get("uvs") or []
            pairs += [(uvs[i], uvs[i + 1]) for i in range(0, len(uvs) - 1, 2)]
        if pairs:
            raw_uvs[node["name"]] = pairs
    for child in node.get("children") or []:
        walk(child)


walk(model.get("graph") or {})
for obj in pc2_entity.primitive_objects():
    name = str(obj[pc2_entity.MESH_PROP])
    pairs = raw_uvs.get(name)
    if not pairs:
        continue
    us = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]
    want = (min(us), min(vs), max(us) - min(us), max(vs) - min(vs))
    got = pc2_entity.mesh_uv_src(obj.data)
    H.check("%s uvSrc" % name, all(abs(a - b) < 1e-4 for a, b in zip(got, want)),
            "got %s want %s" % ([round(v, 4) for v in got], [round(v, 4) for v in want]))

H.section("[2] authoring and export")
bpy.ops.pc2.new_entity(name="UVTEST")
bpy.ops.pc2.add_part(mesh_name="mesh_cube")
cube = bpy.context.view_layer.objects.active
cube.name = "tiled"
cube.pc2.draws = 'TILE'
cube.pc2.uv_tile_u = 2
cube.pc2.uv_tile_v = 3

bpy.ops.pc2.add_part(mesh_name="mesh_hemicylinder")
cyl = bpy.context.view_layer.objects.active
cyl.name = "banded"
cyl.location = (2.0, 0.0, 0.0)
cyl.pc2.uv_repeat_v = 4.0

result = bpy.ops.pc2.export_entity()
H.check("export finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_uv.js"), encoding="utf-8").read()
print(text)
parts = pc2_entity.parse_blueprint(text, "UVTEST")
by_mesh = {p["mesh"]: p for p in parts}
H.check("tile emitted", by_mesh["mesh_cube"].get("uv") == {"tile": {"u": 2, "v": 3}},
        str(by_mesh["mesh_cube"].get("uv")))
H.check("default size omitted", "size" not in str(by_mesh["mesh_cube"].get("uv")))
H.check("repeat emitted", by_mesh["mesh_hemicylinder"].get("uv") == {"repeatV": 4},
        str(by_mesh["mesh_hemicylinder"].get("uv")))

cube.pc2.uv_tile_size = '32'
H.check("non-default size emitted",
        pc2_entity.uv_spec_of(cube) == "{ tile: { u: 2, v: 3, size: 32 } }",
        pc2_entity.uv_spec_of(cube))
cube.pc2.uv_tile_size = str(pc2_entity.DEFAULT_TILE_SIZE)

H.section("[3] tile size resizes the SELECTION, never the picture")
H.check("the sizes on offer", pc2_entity.TILE_SIZES == ("8", "16", "32", "64"),
        str(pc2_entity.TILE_SIZES))
_, atlas_img = pc2_entity.base_texture_image(cube)
w, h = atlas_img.size[0], atlas_img.size[1]
cells = (w // pc2_entity.CELL, h // pc2_entity.CELL)
for size in pc2_entity.TILE_SIZES:
    cube.pc2.uv_tile_size = size
    cube.pc2.uv_tile_u = cube.pc2.uv_tile_v = 1
    # The grid a button can draw is fixed; a grid that followed the tile size
    # would shrink the whole texture to a sixteenth at size 64.
    H.check("size %s keeps the atlas at %dx%d cells" % (size, *cells),
            (w // pc2_entity.CELL, h // pc2_entity.CELL) == cells)
    rect = pc2_entity.tile_rect(1, 1, int(size), h)
    H.check("...and the marker is %s texels" % size,
            rect[2] == int(size) and rect[3] == int(size), str(rect))
cube.pc2.uv_tile_u = 8
cube.pc2.uv_tile_size = '64'
H.check("size 64 clamps tile u onto the 2x2 grid", cube.pc2.uv_tile_u == 2,
        str(cube.pc2.uv_tile_u))
cube.pc2.uv_tile_size = str(pc2_entity.DEFAULT_TILE_SIZE)
cube.pc2.uv_tile_u, cube.pc2.uv_tile_v = 2, 3

H.section("[4] preview node graph vs the shader")


def eval_input(socket, uv):
    if socket.links:
        return eval_socket(socket.links[0].from_socket, uv)
    value = socket.default_value
    return tuple(value)[:3] if hasattr(value, "__len__") else float(value)


def eval_socket(socket, uv):
    node = socket.node
    if node.type in ('UVMAP', 'TEX_COORD'):
        return (uv[0], uv[1], 0.0)
    if node.type == 'SEPXYZ':
        return eval_input(node.inputs[0], uv)["XYZ".index(socket.name)]
    if node.type == 'COMBXYZ':
        return tuple(eval_input(node.inputs[i], uv) for i in range(3))
    if node.type == 'MATH':
        a, b = eval_input(node.inputs[0], uv), eval_input(node.inputs[1], uv)
        if node.operation == 'SUBTRACT':
            return a - b
        raise AssertionError("unhandled math op " + node.operation)
    if node.type == 'VECT_MATH':
        a = eval_input(node.inputs[0], uv)
        if node.operation == 'FRACTION':
            return tuple(x - math.floor(x) for x in a)
        b = eval_input(node.inputs[1], uv)
        ops = {'ADD': lambda x, y: x + y, 'SUBTRACT': lambda x, y: x - y,
               'MULTIPLY': lambda x, y: x * y, 'DIVIDE': lambda x, y: x / y}
        return tuple(ops[node.operation](a[i], b[i]) for i in range(3))
    raise AssertionError("unhandled node type " + node.type)


def graph_uv(obj, uv):
    """What the Blender material samples the image at."""
    index = pc2_entity.textured_slot_index(obj)
    mat = obj.material_slots[index].material
    tex = next(n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE')
    return eval_socket(tex.inputs["Vector"].links[0].from_socket, uv)[:2]


def shader_uv(obj, uv):
    """What the engine samples, expressed as a Blender image coordinate."""
    p = obj.pc2
    src = pc2_entity.mesh_uv_src(obj.data)
    tex_w, tex_h = pc2_entity.texture_dims(obj)
    dst = pc2_entity.uv_dest_rect(p, src, tex_w, tex_h)
    out = pc2_entity.retile((uv[0], 1.0 - uv[1]), src, dst,
                            (p.uv_repeat_u, p.uv_repeat_v))
    return (out[0], 1.0 - out[1])


for obj, label in ((cube, "tile 2,3"), (cyl, "repeatV 4")):
    index = pc2_entity.textured_slot_index(obj)
    H.check("%s uses an OBJECT-linked slot" % label,
            obj.material_slots[index].link == 'OBJECT', obj.material_slots[index].link)
    worst = 0.0
    for i in range(11):
        for j in range(11):
            uv = (i / 10.0, j / 10.0)
            got, want = graph_uv(obj, uv), shader_uv(obj, uv)
            worst = max(worst, abs(got[0] - want[0]), abs(got[1] - want[1]))
    H.check("%s graph matches shader over 121 samples" % label, worst < 1e-5,
            "max delta %.2e" % worst)

# retiling onto the mesh's OWN atlas cell must be an exact no-op -- an
# independent check of the V direction that never mentions retile()
src = pc2_entity.mesh_uv_src(cube.data)
cube.pc2.uv_tile_u = int(round(src[0] * 128 / 16)) + 1
cube.pc2.uv_tile_v = int(round(src[1] * 128 / 16)) + 1
worst = 0.0
for i in range(1, 10):
    for j in range(1, 10):
        uv = (src[0] + src[2] * i / 10.0, 1.0 - src[1] - src[3] * j / 10.0)
        got = graph_uv(cube, uv)
        worst = max(worst, abs(got[0] - uv[0]), abs(got[1] - uv[1]))
H.check("self-tile is the identity", worst < 1e-5, "max delta %.2e" % worst)

H.check("per-instance, not per-mesh",
        cube.material_slots[pc2_entity.textured_slot_index(cube)].material
        is not cyl.material_slots[pc2_entity.textured_slot_index(cyl)].material)

H.section("[5] tile occupancy")
# Most of this atlas is the transparent index, which the shader discards.
filled = {}
cube.pc2.draws = 'TILE'
for v in range(1, 9):
    for u in range(1, 9):
        cube.pc2.uv_tile_u, cube.pc2.uv_tile_v = u, v
        filled[(u, v)] = pc2_entity.tile_occupancy(cube)
# WHICH tiles hold art is content and moves as the atlas is repainted -- most
# of it was stripped in f16227f, on the "solid colours first, texture only
# where detail earns it" direction -- so pin the INVARIANT rather than the
# layout: occupancy must agree with an independent top-down count straight off
# model.txt. That flip is the one thing a tile picker gets silently wrong
# (Blender's buffers run bottom-up, the engine numbers tiles from the top) and
# a mirrored marker would still box a plausible-looking piece of texture.
# Naming the cells instead made this fail on an atlas edit that broke nothing.
tex = json.load(open(H.MODEL, encoding="utf-8"))["texture"]
pixels, clear = tex["pixels"], tex["transparent_color"]
TEX_W = int(round(len(pixels) ** 0.5))


def counted(u, v):
    """Painted texels in one 16-texel cell, top-down, from the raw hex grid."""
    n = 0
    for y in range((v - 1) * 16, v * 16):
        for x in range((u - 1) * 16, u * 16):
            if int(pixels[y * TEX_W + x], 16) != clear:
                n += 1
    return n


wrong = {cell: (filled[cell], counted(*cell) / 256.0)
         for cell in sorted(filled)
         if filled[cell] is None or abs(filled[cell] - counted(*cell) / 256.0) > 1e-6}
H.check("occupancy agrees with a top-down count of model.txt, all 64 tiles",
        not wrong, str(wrong) if wrong else
        "%d of 64 tiles hold art" % sum(1 for c in filled if filled[c]))
H.check("and it finds the art that IS there -- an all-empty atlas proves nothing",
        any(filled[c] for c in filled), str(sorted(c for c in filled if filled[c])))

H.section("[6] clear")
for o in bpy.context.selected_objects:
    o.select_set(False)
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
bpy.ops.pc2.clear_uv()
H.check("spec gone", pc2_entity.uv_spec_of(cube) is None, str(pc2_entity.uv_spec_of(cube)))
H.check("mode back to Texture", cube.pc2.draws == 'TEXTURE', cube.pc2.draws)
H.check("slot back to the shared material",
        cube.material_slots[pc2_entity.textured_slot_index(cube)].link == 'DATA')

H.section("[7] a uv shape Blender cannot author is carried verbatim")
pc2_entity.apply_uv_spec(cyl, {"rect": [0.25, 0.5, 0.125, 0.125]})
H.check("stashed verbatim",
        cyl.get(pc2_entity.UV_PROP) == "{ rect: [0.25, 0.5, 0.125, 0.125] }",
        str(cyl.get(pc2_entity.UV_PROP)))
H.check("and re-exported unchanged",
        pc2_entity.uv_spec_of(cyl) == "{ rect: [0.25, 0.5, 0.125, 0.125] }")

H.section("[8] colour: the model's palette, and what a swatch does")
palette = pc2_entity.model_palette(H.MODEL)
H.check("16 colours read out of model.txt", len(palette) == 16, str(len(palette)))
H.check("they are the same array pico.js reads",
        palette == tuple(tuple(c) for c in
                         json.load(open(H.MODEL, encoding="utf-8"))["texture"]["colors"][:16]))

for o in bpy.context.selected_objects:
    o.select_set(False)
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
H.check("no override to begin with", pc2_entity.color_of(cube) is None)
result = bpy.ops.pc2.set_color(index=9)
H.check("operator finished", result == {'FINISHED'}, str(result))
H.check("the part carries the index", pc2_entity.color_of(cube) == 9,
        str(pc2_entity.color_of(cube)))
# The engine draws a colour-overridden instance FLAT, so the preview must be
# flat too -- and per-object, or every part sharing the mesh would recolour.
H.check("every slot went OBJECT-linked to a flat material",
        all(s.link == 'OBJECT' and s.material is not None
            and s.material.name == "pc2_color_unlit_9" for s in cube.material_slots),
        str([(s.link, s.material.name if s.material else None)
             for s in cube.material_slots]))
H.check("the other part is untouched", pc2_entity.color_of(cyl) is None)

bpy.ops.pc2.export_entity()
text = open(H.out("entities_uv.js"), encoding="utf-8").read()
parts = pc2_entity.parse_blueprint(text, "UVTEST")
H.check("exported as color: 9",
        any(p.get("color") == 9 for p in parts), str(parts))

result = bpy.ops.pc2.set_color(index=-1)
H.check("clearing drops it", pc2_entity.color_of(cube) is None and result == {'FINISHED'})
H.check("and hands the slots back to the mesh",
        all(s.link == 'DATA' for s in cube.material_slots))

H.section("[9] the atlas marker boxes the tile the shader samples")
# The marker and the shader's rect are built from opposite row orders, so a
# wrong flip would box a plausible-looking but WRONG tile every time.
# tile_occupancy() walks the atlas top-down and independently.
_, atlas = pc2_entity.base_texture_image(cube)
px = pc2_entity.image_pixels(atlas)
size = 16
w, h = atlas.size[0], atlas.size[1]
cube.pc2.draws = 'TILE'
mismatched = []
for v in range(1, h // size + 1):
    for u in range(1, w // size + 1):
        x0, y0, tw, th = pc2_entity.tile_rect(u, v, size, h)
        drawn = sum(1 for y in range(y0, y0 + th) for x in range(x0, x0 + tw)
                    if px[(y * w + x) * 4 + 3] > 0.5)
        cube.pc2.uv_tile_u, cube.pc2.uv_tile_v = u, v
        if abs(drawn / (tw * th) - pc2_entity.tile_occupancy(cube)) > 1e-6:
            mismatched.append((u, v))
H.check("every tile's box holds the texels the engine samples", not mismatched,
        str(mismatched[:6]))
H.check("tile 1,1 is the TOP-left of the atlas",
        pc2_entity.tile_rect(1, 1, size, h) == (0, h - size, size, size),
        str(pc2_entity.tile_rect(1, 1, size, h)))
H.check("the atlas base is one RGBA quad per texel and fully opaque",
        len(pc2_entity.atlas_base(atlas)) == w * h * 4
        and all(pc2_entity.atlas_base(atlas)[i * 4 + 3] == 1.0 for i in range(0, w * h, 97)))

cube.pc2.uv_tile_u, cube.pc2.uv_tile_v = 3, 2
H.check("the fields are what gets exported",
        pc2_entity.uv_spec_of(cube) == "{ tile: { u: 3, v: 2 } }",
        str(pc2_entity.uv_spec_of(cube)))

H.section("[10] colour and tile take turns, and neither forgets")
for o in bpy.context.selected_objects:
    o.select_set(False)
cube.select_set(True)
bpy.context.view_layer.objects.active = cube
bpy.ops.pc2.pick_tile(u=4, v=1)
H.check("picking a tile puts it in effect",
        cube.pc2.draws == 'TILE' and (cube.pc2.uv_tile_u, cube.pc2.uv_tile_v) == (4, 1),
        cube.pc2.draws)
H.check("and it is what gets exported",
        pc2_entity.uv_spec_of(cube) == "{ tile: { u: 4, v: 1 } }"
        and pc2_entity.active_color(cube) is None,
        str(pc2_entity.uv_spec_of(cube)))

bpy.ops.pc2.set_color(index=7)
H.check("a colour takes over", cube.pc2.draws == 'COLOR', cube.pc2.draws)
H.check("the tile is remembered, not lost",
        (cube.pc2.uv_tile_u, cube.pc2.uv_tile_v) == (4, 1))
# The engine gives a colour-overridden instance no texture lookup, so shipping
# the retile alongside would describe something it never draws.
H.check("only the colour is exported",
        pc2_entity.active_color(cube) == 7 and pc2_entity.uv_spec_of(cube) is None,
        "%s %s" % (pc2_entity.active_color(cube), pc2_entity.uv_spec_of(cube)))

bpy.ops.pc2.pick_tile(u=4, v=1)
H.check("the tile takes it back", cube.pc2.draws == 'TILE')
H.check("the colour is remembered, not lost", pc2_entity.part_color(cube) == 7)
H.check("and only the tile is exported",
        pc2_entity.active_color(cube) is None
        and pc2_entity.uv_spec_of(cube) == "{ tile: { u: 4, v: 1 } }")

cube.pc2.draws = 'TEXTURE'
H.check("Texture drops both", pc2_entity.active_color(cube) is None
        and pc2_entity.uv_spec_of(cube) is None)
H.check("while still remembering both",
        pc2_entity.part_color(cube) == 7
        and (cube.pc2.uv_tile_u, cube.pc2.uv_tile_v) == (4, 1))
bpy.ops.pc2.set_color(index=-1)
H.check("the X forgets the colour", pc2_entity.part_color(cube) == -1)

H.section("[11] a colour applies to every selected part")
cyl.select_set(True)
bpy.ops.pc2.set_color(index=3)
H.check("both parts took it",
        pc2_entity.color_of(cube) == 3 and pc2_entity.color_of(cyl) == 3,
        "%s %s" % (pc2_entity.color_of(cube), pc2_entity.color_of(cyl)))

H.finish()
