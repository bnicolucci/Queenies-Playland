"""Parent To Active must never inherit the parent's scale.

A sphere attached to a 1x2.8x1 pillar stays a sphere -- in Blender AND in the
engine -- including when rotated, and after the pillar is rescaled and Drop
Inherited Scale is re-run.
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
props.entities_path = H.out("entities_noscale.js")
if os.path.exists(H.out("entities_noscale.js")):
    os.remove(H.out("entities_noscale.js"))


def world_scale(obj):
    return list(obj.matrix_world.decompose()[2])


def uniform(obj, tol=1e-4):
    return all(abs(v - 1.0) < tol for v in world_scale(obj))


bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name="NOSCALE")

bpy.ops.pc2.add_part(mesh_name="mesh_cube")
pillar = bpy.context.view_layer.objects.active
pillar.name = "pillar"
pillar.scale = (1.0, 2.8, 1.0)
bpy.context.view_layer.update()

H.section("[1] attach a sphere with Parent To Active")
props.parent_to_active = True
bpy.ops.pc2.add_part(mesh_name="mesh_hemisphere")
head = bpy.context.view_layer.objects.active
head.name = "head"
head.location = (0.0, 0.0, 1.2)
bpy.context.view_layer.update()
H.check("head is parented", head.parent is pillar)
H.check("head world scale is 1,1,1 (stays a sphere)", uniform(head), str(world_scale(head)))

H.section("[2] rotate it -- still a sphere, still no shear in world")
head.rotation_euler = Euler([math.radians(v) for v in (25.0, 40.0, 10.0)], 'XYZ')
bpy.context.view_layer.update()
H.check("still uniform after rotation", uniform(head), str(world_scale(head)))
_, _, _, world_exact = pc2_entity.decompose_part(head, world=True)
H.check("world matrix has no shear", world_exact)

H.section("[3] the parent still carries the child around")
pillar.location = (1.0, -0.5, 0.0)
bpy.context.view_layer.update()
H.check("head followed the pillar",
        abs(head.matrix_world.translation.x - 1.0) < 1e-4
        and abs(head.matrix_world.translation.y + 0.5) < 1e-4,
        str(list(head.matrix_world.translation)))
H.check("and is still a sphere", uniform(head), str(world_scale(head)))

H.section("[4] rescaling the pillar leaks scale back in; the operator re-fixes it")
pillar.scale = (1.0, 4.5, 1.0)
bpy.context.view_layer.update()
H.check("leak happens as documented (baked, not live)", not uniform(head),
        str(world_scale(head)))
for obj in bpy.context.selected_objects:
    obj.select_set(False)
head.select_set(True)
bpy.context.view_layer.objects.active = head
result = bpy.ops.pc2.drop_inherited_scale()
bpy.context.view_layer.update()
H.check("operator finished", result == {'FINISHED'}, str(result))
H.check("uniform again", uniform(head), str(world_scale(head)))

H.section("[5] export")
props.entity = bpy.data.collections.get("NOSCALE")
result = bpy.ops.pc2.export_entity()
H.check("operator finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_noscale.js"), encoding="utf-8").read()
print(text)
parts = pc2_entity.parse_blueprint(text, "NOSCALE")
H.check("head exported with scale 1 (or none)",
        parts[1].get("scale", [1, 1, 1]) == [1, 1, 1], str(parts[1]))

H.dump_expected(pc2_entity, "expected_noscale.json", (pillar, head))
H.finish()
