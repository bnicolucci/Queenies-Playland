"""Shear: a non-uniformly scaled parent with a rotated child, parented the way
plain Ctrl+P does it (non-identity parent inverse).

Blender draws that unskewed because the inverse cancels the parent's scale; the
child's LOCAL matrix carries shear no TRS part can hold. Export must flatten it
to a root with its world transform rather than approximate it.
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
props.entities_path = H.out("entities_shear.js")
if os.path.exists(H.out("entities_shear.js")):
    os.remove(H.out("entities_shear.js"))

bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name="SHEARTEST")

bpy.ops.pc2.add_part(mesh_name="mesh_cube")
body = bpy.context.view_layer.objects.active
body.name = "body"
body.scale = (1.0, 2.8, 1.0)
body.location = (0.0, 0.0, 1.0)

props.parent_to_active = False
bpy.ops.pc2.add_part(mesh_name="mesh_cube")
arm = bpy.context.view_layer.objects.active
arm.name = "arm"
arm.location = (0.6, 0.0, 1.4)
arm.rotation_euler = Euler([math.radians(v) for v in (30.0, 0.0, 20.0)], 'XYZ')
arm.scale = (0.4, 0.4, 1.1)
bpy.context.view_layer.update()
arm.parent = body
arm.matrix_parent_inverse = body.matrix_world.inverted()   # == plain Ctrl+P
bpy.context.view_layer.update()

H.section("[1] the shear is real")
_, _, _, local_exact = pc2_entity.decompose_part(arm)
_, _, _, world_exact = pc2_entity.decompose_part(arm, world=True)
H.check("arm's LOCAL matrix carries shear", not local_exact)
H.check("arm's WORLD matrix does not", world_exact)

H.section("[2] export")
result = bpy.ops.pc2.export_entity()
H.check("operator finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_shear.js"), encoding="utf-8").read()
print(text)
parts = pc2_entity.parse_blueprint(text, "SHEARTEST")
H.check("arm emitted as a root (flattened)", "parent" not in parts[1], str(parts[1]))
# Blender scale (1, 2.8, 1) is engine (1, 1, 2.8): Y and Z swap.
H.check("body keeps its own transform", parts[0].get("scale") == [1, 1, 2.8],
        str(parts[0]))

H.section("[3] a flattened part can still carry states")
# The arm is exported as a ROOT, so its state has to be composed up the Blender
# chain too -- otherwise moving the body would leave the arm's stored LOCAL
# being read against a parent the blueprint no longer has.
bpy.ops.pc2.new_state(name="LIFTED")
body.rotation_euler = Euler([math.radians(25.0), 0.0, 0.0], 'XYZ')
body.location = (0.0, 0.0, 1.6)
bpy.context.view_layer.update()
expected = pc2_entity.conjugate(arm.matrix_world)
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
result = bpy.ops.pc2.export_entity()
H.check("stateful export with a flattened part finishes", result == {'FINISHED'}, str(result))
text = open(H.out("entities_shear.js"), encoding="utf-8").read()
print(text)
parts = pc2_entity.parse_blueprint(text, "SHEARTEST")
states = pc2_entity.parse_states(text, "SHEARTEST")
H.check("one state written", len(states) == 1, str(states))
by_index = {int(delta[0]): delta for delta in states[0][1]}
H.check("the body moved in the state", 0 in by_index, str(states[0]))
H.check("the arm followed it as an override", 1 in by_index, str(states[0]))
got = pc2_entity.state_local(parts[1], by_index.get(1))
H.check("the arm's override IS its composed world matrix",
        all(abs(a - b) < 1e-3 for row_g, row_e in zip(got, expected)
            for a, b in zip(row_g, row_e)),
        "%s vs %s" % (list(got), list(expected)))

H.dump_expected(pc2_entity, "expected_shear.json", (body, arm))
H.finish()
