"""Pivots: a part that draws nothing, there to be a parent.

Blender's name for it is an Empty; the blueprint's is a part with no `mesh`.
The failure this exists to prevent is the one that turned up in authoring: the
addon used to filter an entity's parts with mesh_name_of, which says None for
an Empty, so a pivot was dropped on export -- and its child, whose parent had
vanished, was re-emitted as a ROOT still carrying a matrix that was local to
the pivot. The whole sub-assembly landed at the entity origin.

Covers the round trip too (export -> forget the collection -> import -> export
is byte-identical) because an Empty is made by a different code path from a
mesh part, and the browser check that follows runs the blueprint through the
REAL spawnEntity.
"""
import math
import os
import sys

import bpy
from mathutils import Euler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.out("entities_pivot.js")
if os.path.exists(H.out("entities_pivot.js")):
    os.remove(H.out("entities_pivot.js"))

bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name="PIVOTTEST")

H.section("[1] build body -> pivot -> jaw")
props.parent_to_active = False
bpy.ops.pc2.add_part(mesh_name="mesh_hemicylinder")
body = bpy.context.view_layer.objects.active
body.name = "body"
body.location = (0.0, 0.0, 0.5)
bpy.context.view_layer.update()

props.parent_to_active = True
result = bpy.ops.pc2.add_pivot()
hinge = bpy.context.view_layer.objects.active
H.check("operator finished", result == {'FINISHED'}, str(result))
H.check("it is an Empty", hinge.type == 'EMPTY', hinge.type)
H.check("mesh_name_of says None", pc2_entity.mesh_name_of(hinge) is None)
H.check("is_pivot says yes", pc2_entity.is_pivot(hinge))
H.check("is_part says yes -- this is what stops the export dropping it",
        pc2_entity.is_part(hinge))
H.check("it parented to the active part", hinge.parent is body)
hinge.name = "hinge"
# The hinge sits at the back of the head, which is the entire point: the jaw
# has to turn about THIS, not about its own centre.
hinge.location = (0.0, -0.35, 0.45)
hinge.rotation_euler = Euler([math.radians(v) for v in (0.0, 0.0, 12.0)], 'XYZ')
bpy.context.view_layer.update()

bpy.context.view_layer.objects.active = hinge
bpy.ops.pc2.add_part(mesh_name="mesh_hemisphere")
jaw = bpy.context.view_layer.objects.active
jaw.name = "jaw"
jaw.location = (0.0, 0.4, 0.0)
jaw.scale = (0.6, 0.6, 0.35)
bpy.context.view_layer.update()
H.check("jaw hangs off the pivot", jaw.parent is hinge)

coll = bpy.data.collections.get("PIVOTTEST")
H.check("the panel counts 3 parts",
        len([o for o in coll.all_objects if pc2_entity.is_part(o)]) == 3)

H.section("[2] export")
props.entity = coll
result = bpy.ops.pc2.export_entity()
H.check("operator finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_pivot.js"), encoding="utf-8").read()
print(text)
blueprint = pc2_entity.parse_blueprint(text, "PIVOTTEST")
H.check("3 parts exported", len(blueprint) == 3, str(len(blueprint)))
H.check("the pivot has no mesh key", "mesh" not in blueprint[1], str(blueprint[1]))
# Blender Z is engine Y (see the C basis change), so the 12 deg turn about
# Blender's Z exports as rot [0, 12, 0].
H.check("the pivot kept its transform",
        blueprint[1].get("rot") == [0, 12, 0] and blueprint[1].get("pos") is not None,
        str(blueprint[1]))
H.check("the jaw points AT the pivot, not at nothing",
        blueprint[2].get("parent") == 1, str(blueprint[2].get("parent")))
H.check("and the jaw still draws a mesh", blueprint[2].get("mesh") == "mesh_hemisphere",
        str(blueprint[2].get("mesh")))

# The pivot's NAME rides out as a trailing comment rather than a field. A
# comment is free -- the minifier drops it before the bundle is compressed --
# where an `n:` field measured ~5 zip bytes per pivot.
H.check("the pivot's name came out as a comment", "},   // hinge" in text,
        "; ".join(ln.strip() for ln in text.splitlines()
                  if "//" in ln and ln.strip().startswith("{")))
H.check("and it is not a field", "n:" not in text and "'hinge'" not in text)
# Every part carries its outliner name out, mesh parts included -- that is
# what makes the blueprint readable when a wheel has eight mesh_slices in it,
# and what stops an index like `wheel[2]` being the only handle on a part.
H.check("labels parse back out by part index",
        pc2_entity.blueprint_labels(text, "PIVOTTEST") == {0: "body", 1: "hinge", 2: "jaw"},
        str(pc2_entity.blueprint_labels(text, "PIVOTTEST")))

H.section("[3] which way a Blender rotation goes in the engine")
# C maps a Blender direction (x, y, z) to engine (-x, z, y), so Blender's X
# axis is engine -X: an X rotation comes out NEGATED, while Y and Z simply
# swap places keeping their sign. Getting this backwards is what makes a jaw
# hinge into the body instead of away from it, and nothing else in the suite
# says it out loud.
probe = pc2_entity.new_pivot("axis_probe")
coll.objects.link(probe)
for axis, expected in ((0, [-30, 0, 0]), (1, [0, 0, 30]), (2, [0, 30, 0])):
    euler = [0.0, 0.0, 0.0]
    euler[axis] = math.radians(30.0)
    probe.rotation_euler = Euler(euler, 'XYZ')
    bpy.context.view_layer.update()
    _, rot, _, _ = pc2_entity.decompose_part(probe)
    got = [round(v, 3) + 0.0 for v in rot]
    H.check("Blender +30 about %s -> engine rot %s" % ("XYZ"[axis], expected),
            all(abs(a - b) < 1e-2 for a, b in zip(got, expected)), str(got))
bpy.data.objects.remove(probe, do_unlink=True)

H.section("[4] forget it and read it back")
# Import builds an Empty by a different path from a mesh part, so the round
# trip is the only thing that checks that path at all.
# A MESH part's label is authored the same way a pivot's is -- it is the object
# NAME -- so the file already holds one for every part and nothing needs
# injecting here.
before = pc2_entity.entity_block(text, "PIVOTTEST")
for obj in list(coll.all_objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.data.collections.remove(coll)
H.check("collection gone", bpy.data.collections.get("PIVOTTEST") is None)

result = bpy.ops.pc2.edit_entity(name="PIVOTTEST")
H.check("re-imported", result == {'FINISHED'}, str(result))
coll = bpy.data.collections.get("PIVOTTEST")
empties = [o for o in coll.all_objects if o.type == 'EMPTY']
H.check("the pivot came back as an Empty", len(empties) == 1,
        str([(o.name, o.type) for o in coll.all_objects]))
H.check("named from its label, which is where you rename it",
        empties[0].name == "hinge", empties[0].name)
body = next(o for o in coll.all_objects if pc2_entity.mesh_name_of(o) == "mesh_hemicylinder")
H.check("a mesh part came back named from its label too",
        pc2_entity.SUFFIX_RE.sub("", body.name) == "body", body.name)
H.check("and the name did not lose which primitive it draws",
        pc2_entity.mesh_name_of(body) == "mesh_hemicylinder",
        str(pc2_entity.mesh_name_of(body)))
reimported_jaw = next(o for o in coll.all_objects if pc2_entity.mesh_name_of(o) == "mesh_hemisphere")
H.check("still parented to it", reimported_jaw.parent is empties[0],
        str(reimported_jaw.parent and reimported_jaw.parent.name))

props.entity = coll
result = bpy.ops.pc2.export_entity()
H.check("re-exported", result == {'FINISHED'}, str(result))
after = pc2_entity.entity_block(open(H.out("entities_pivot.js"), encoding="utf-8").read(),
                                "PIVOTTEST")
H.check("byte-identical round trip", before == after)
if before != after:
    print("       before:\n%s\n       after:\n%s" % (before, after))

order = [next(o for o in coll.all_objects if pc2_entity.mesh_name_of(o) == "mesh_hemicylinder"),
         empties[0], reimported_jaw]
H.dump_expected(pc2_entity, "expected_pivot.json", order)

H.section("[5] Add Pivot from inside Edit Mode")
# The workflow that places a hinge: Tab into a part, pick a vertex, snap the
# cursor to it, Add Pivot. Making the new Empty active while the part is still
# in Edit Mode leaves the part flagged edit with no edit-mesh behind it, and
# Blender crashes in the edit overlay on a later redraw or undo (two crash
# logs, Sep 2026). place_new_part has to leave Edit Mode first.
for o in bpy.context.view_layer.objects:
    o.select_set(False)
body.select_set(True)
bpy.context.view_layer.objects.active = body
bpy.ops.object.mode_set(mode='EDIT')
H.check("the part is in Edit Mode", bpy.context.mode == 'EDIT_MESH', bpy.context.mode)
props.parent_to_active = True
result = bpy.ops.pc2.add_pivot()
H.check("Add Pivot finished", result == {'FINISHED'}, str(result))
H.check("Blender is back in Object Mode", bpy.context.mode == 'OBJECT', bpy.context.mode)
H.check("the part itself is no longer flagged edit", body.mode == 'OBJECT', body.mode)
added = bpy.context.view_layer.objects.active
H.check("the new pivot is active and parented to the part it was added from",
        added.type == 'EMPTY' and added.parent is body,
        "%s -> %s" % (added.name, added.parent and added.parent.name))
H.finish()
