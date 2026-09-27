"""Primitive library, part composition, and the Blender -> engine transform.

The emitted blueprint is composed the way spawnEntity does and diffed against
Blender's own world matrices, so this covers the basis conjugation, the euler
order, and the DFS parent indexing in one assertion.
"""
import math
import os
import sys

import bpy
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.out("entities_test.js")

H.section("[0] euler order")
# The addon claims Blender's 'XYZ' order builds Rz @ Ry @ Rx, which is what
# DOMMatrix.rotate(rx, ry, rz) post-multiplies. verify_engine.ts confirms the
# DOMMatrix half in a real browser.
angles = [math.radians(v) for v in (37.0, 11.0, 53.0)]
manual = (Matrix.Rotation(angles[2], 4, 'Z')
          @ Matrix.Rotation(angles[1], 4, 'Y')
          @ Matrix.Rotation(angles[0], 4, 'X'))
blender = Euler(angles, 'XYZ').to_matrix().to_4x4()
H.check("Euler('XYZ') == Rz @ Ry @ Rx",
        all(abs(x - y) < 1e-6 for rm, rb in zip(manual, blender) for x, y in zip(rm, rb)))

H.section("[1] load primitives")
result = bpy.ops.pc2.load_primitives()
H.check("operator finished", result == {'FINISHED'}, str(result))
prims = pc2_entity.primitive_objects()
names = sorted(str(o[pc2_entity.MESH_PROP]) for o in prims)
H.check("primitives discovered", len(prims) > 0, str(names))
H.check("names match model.txt",
        set(names) == (pc2_entity.model_mesh_names(H.MODEL) or set()), str(names))

H.section("[2] build TESTBOT")
bpy.ops.pc2.new_entity(name="TESTBOT")
coll = props.entity
H.check("entity collection", coll is not None and coll.name == "TESTBOT")
# It used to take a part before an entity appeared anywhere but the outliner.
H.check("a part-less entity is already listed",
        "TESTBOT" in pc2_entity.entity_names(bpy.context)
        and "TESTBOT" in {c.name for c in pc2_entity.entity_collections()})

# root: a squashed half-cylinder, off-origin on all three axes so the axis mapping shows
bpy.ops.pc2.add_part(mesh_name="mesh_hemicylinder")
root = bpy.context.view_layer.objects.active
root.name = "body"
root.location = (0.25, 0.5, 1.0)
root.scale = (0.5, 0.5, 1.2)

# child, rotated on all three axes
props.parent_to_active = True
bpy.ops.pc2.add_part(mesh_name="mesh_hemisphere")
head = bpy.context.view_layer.objects.active
head.name = "head"
head.location = (0.0, 0.0, 0.75)
head.rotation_euler = Euler([math.radians(v) for v in (37.0, 11.0, 53.0)], 'XYZ')

# grandchild, to prove DFS ordering and index remapping
bpy.ops.pc2.add_part(mesh_name="mesh_plane")
antenna = bpy.context.view_layer.objects.active
antenna.name = "antenna"
antenna.location = (0.0, 0.3, 0.4)

H.check("hierarchy", head.parent is root and antenna.parent is head)
H.check("linked instance shares datablock",
        root.data is next(o for o in prims
                          if str(o[pc2_entity.MESH_PROP]) == "mesh_hemicylinder").data)

H.section("[2b] the primitive buttons are the model's meshes")
# A part dropped into the library collection (the old panel's free-form
# collection picker made that easy) used to add a second button for a mesh
# that exists once.
stray = bpy.data.objects.new("mesh_cube", root.data)
stray[pc2_entity.MESH_PROP] = "mesh_cube"
pc2_entity.primitives_collection().objects.link(stray)
buttons = [str(o[pc2_entity.MESH_PROP]) for o in pc2_entity.primitive_objects()]
H.check("one button per mesh, no duplicates", len(buttons) == len(set(buttons)),
        str(buttons))
H.check("still every mesh in the model", set(buttons) == pc2_entity.model_mesh_names(H.MODEL),
        str(buttons))
pc2_entity.primitives_collection().objects.unlink(stray)

H.section("[3] export")
result = bpy.ops.pc2.export_entity()
H.check("operator finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_test.js"), encoding="utf-8").read()
print(text)
H.check("block written", "export const TESTBOT = [" in text)
H.check("parents precede children",
        text.index("'mesh_hemicylinder'") < text.index("'mesh_hemisphere'") < text.index("'mesh_plane'"))

H.section("[4] emitted blueprint vs Blender world matrices")
# Composed the way spawnEntity does. Parts may be flattened to roots (scale-free
# parenting puts shear in the LOCAL matrix), so drive this off the emitted file
# rather than off the Blender hierarchy.
blueprint = pc2_entity.parse_blueprint(text, "TESTBOT")
composed = []
for part in blueprint:
    m = pc2_entity.engine_local(part)
    if "parent" in part:
        m = composed[int(part["parent"])] @ m
    composed.append(m)

for obj, engine_local in zip((root, head, antenna), composed):
    expected = pc2_entity.C @ obj.matrix_world @ pc2_entity.C
    ok = all(abs(x - y) < 1e-4
             for re_, rx in zip(engine_local, expected) for x, y in zip(re_, rx))
    H.check("%s matrix round-trips" % obj.name, ok,
            "" if ok else "got %s | want %s" % (engine_local, expected))
    bl, en = obj.matrix_world.translation, engine_local.translation
    print("       blender (%.2f, %.2f, %.2f) -> engine (%.2f, %.2f, %.2f)"
          % (bl.x, bl.y, bl.z, en.x, en.y, en.z))

H.dump_expected(pc2_entity, "expected.json", (root, head, antenna))

H.section("[5] merge safety")
real = open(H.ENTITIES, encoding="utf-8").read()
merged = pc2_entity.upsert_entity(real, "TESTBOT", "export const TESTBOT = [\n];")
H.check("existing entities survive a merge",
        "export const CAPSULE" in merged and "export const ROCKET" in merged
        and "export const TESTBOT" in merged)
replaced = pc2_entity.upsert_entity(merged, "TESTBOT",
                                    "export const TESTBOT = [\n  { mesh: 'x' },\n];")
H.check("re-export replaces, not appends", replaced.count("export const TESTBOT") == 1)
found = pc2_entity.existing_entity_names(real)
H.check("the file's entity names are found", {"CAPSULE", "ROCKET"} <= found,
        str(sorted(found)))

H.section("[6] one entity in the scene at a time")
# Blueprints are all authored around the origin, so a second entity left linked
# sits inside the first one.


def in_scene():
    return [c.name for c in bpy.context.scene.collection.children
            if c.get(pc2_entity.ENTITY_PROP)]


bpy.ops.pc2.new_entity(name="SECONDBOT")
H.check("both are listed",
        {"TESTBOT", "SECONDBOT"} <= set(pc2_entity.entity_names(bpy.context)))
H.check("only the new one is in the scene", in_scene() == ["SECONDBOT"], str(in_scene()))
H.check("the unlinked one survives with its parts",
        bpy.data.collections.get("TESTBOT") is not None
        and len(bpy.data.collections["TESTBOT"].all_objects) == 3)
result = bpy.ops.pc2.edit_entity(name="TESTBOT")
H.check("switching back re-links it",
        result == {'FINISHED'} and in_scene() == ["TESTBOT"],
        "%s %s" % (result, in_scene()))
try:
    taken = bpy.ops.pc2.new_entity(name="TESTBOT")
except RuntimeError:
    taken = {'CANCELLED'}   # Blender would have made TESTBOT.001
H.check("New Entity refuses a name that is taken", taken == {'CANCELLED'}, str(taken))

H.finish()
