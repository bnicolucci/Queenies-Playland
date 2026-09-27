"""Compound primitives: one button, several parts.

A "sphere" here is two hemispheres base to base, so model.txt can ship one
round mesh instead of two. The thing worth testing is not that two objects
appear -- it is that the pair behaves like ONE primitive afterwards, which is
entirely a question of parenting:

  * the halves must meet exactly at the equator and close into a sphere,
  * a scaled root must carry the lower half with it (scale-free parenting,
    which every other Add button uses, would leave a full-size half inside a
    squashed one),
  * that scale must not manufacture shear, which a TRS part cannot store,
  * and Mirror and Drop Inherited Scale -- both of which reach for the
    scale-free parent inverse -- must leave a compound's inner parts alone.

Finally it exports, because the whole design rests on a compound being
ORDINARY parts: entity.js never learns the word, so the browser check that
follows runs this blueprint through the real spawnEntity like any other.
"""
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.out("entities_compound.js")
if os.path.exists(H.out("entities_compound.js")):
    os.remove(H.out("entities_compound.js"))

bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name="COMPOUNDTEST")
coll = bpy.context.scene.pc2_entity.entity


def world_bounds(objs):
    """Axis-aligned bounds in Blender space, which is z-up: the engine's y --
    the sphere's poles -- is Blender's z."""
    lo, hi = [1e9] * 3, [-1e9] * 3
    for obj in objs:
        for corner in obj.bound_box:
            v = obj.matrix_world @ Vector(corner)
            for k in range(3):
                lo[k], hi[k] = min(lo[k], v[k]), max(hi[k], v[k])
    return lo, hi


H.section("[1] one button stamps the assembly")
result = bpy.ops.pc2.add_compound(name="sphere")
H.check("operator finished", result == {'FINISHED'}, str(result))
parts = [o for o in coll.all_objects if pc2_entity.is_part(o)]
H.check("two parts", len(parts) == 2, str([o.name for o in parts]))
root = next(o for o in parts if o.parent is None)
lower = next(o for o in parts if o.parent is not None)
H.check("the second is parented to the first", lower.parent is root)
H.check("named for the compound, so the outliner reads 'sphere'",
        root.name == "sphere" and lower.name.startswith("sphere."),
        "%s / %s" % (root.name, lower.name))
H.check("both instance the one mesh",
        pc2_entity.mesh_name_of(root) == pc2_entity.mesh_name_of(lower) == "mesh_hemisphere")
H.check("they share the datablock -- a linked duplicate, which is what the "
        "engine instances", root.data is lower.data)
# Scaling BOTH would square the inner one, since it already follows the root.
H.check("only the root is left selected, because it is the handle",
        bpy.context.view_layer.objects.active is root
        and root.select_get() and not lower.select_get())
H.check("the inner part is marked rigid", bool(lower.get(pc2_entity.RIGID_PROP)))
H.check("the root is not", not root.get(pc2_entity.RIGID_PROP))

H.section("[2] the halves make a closed sphere")
bpy.context.view_layer.update()
top, bottom = world_bounds([root]), world_bounds([lower])
# Compared against each other, never against a radius typed in here: the
# mesh's own 0.510113 is not a number this test should know.
H.check("they meet at the equator",
        abs(top[0][2]) < 1e-6 and abs(bottom[1][2]) < 1e-6,
        "top starts %.6f, bottom ends %.6f" % (top[0][2], bottom[1][2]))
H.check("and reach the same distance either side of it",
        abs(top[1][2] + bottom[0][2]) < 1e-6,
        "z [%.6f, %.6f]" % (bottom[0][2], top[1][2]))

H.section("[3] scaling the root scales the whole shape")
radius = top[1][2]
root.scale = (1.0, 1.0, 2.0)
bpy.context.view_layer.update()
lo, hi = world_bounds(parts)
H.check("the lower half went with it",
        abs(hi[2] - 2 * radius) < 1e-6 and abs(lo[2] + 2 * radius) < 1e-6,
        "z [%.6f, %.6f] against a radius of %.6f" % (lo[2], hi[2], radius))
H.check("an ellipsoid, not an egg -- the untouched axis did not move",
        abs(hi[0] - 0.5) < 1e-3 and abs(lo[0] + 0.5) < 1e-3,
        "x [%.6f, %.6f]" % (lo[0], hi[0]))
# A half turn is a sign flip, and a sign flip commutes with an axis-aligned
# scale. A quarter turn here would shear the moment the root was scaled
# unevenly, and a TRS part has nowhere to put shear.
_, _, _, exact = pc2_entity.decompose_matrix(pc2_entity.conjugate(lower.matrix_world))
H.check("no shear in the squashed lower half", exact)

H.section("[4] the two operators that re-parent leave it alone")
for obj in bpy.context.selected_objects:
    obj.select_set(False)
root.select_set(True)
bpy.context.view_layer.objects.active = root
bpy.ops.pc2.mirror_parts()
copies = [o for o in coll.all_objects if pc2_entity.is_part(o) and o not in parts]
H.check("Mirror copied the whole compound", len(copies) == 2,
        str([o.name for o in copies]))
copy_lower = next((o for o in copies if o.parent is not None), None)
H.check("and the copy still inherits its parent's scale",
        copy_lower is not None
        and copy_lower.matrix_parent_inverse == Matrix.Identity(4)
        and bool(copy_lower.get(pc2_entity.RIGID_PROP)))
for obj in copies:
    bpy.data.objects.remove(obj, do_unlink=True)

for obj in bpy.context.selected_objects:
    obj.select_set(False)
lower.select_set(True)
bpy.context.view_layer.objects.active = lower
before = lower.matrix_parent_inverse.copy()
try:
    bpy.ops.pc2.drop_inherited_scale()
except RuntimeError:
    pass   # it reports an error, which is the point
H.check("Drop Inherited Scale refused to un-glue it",
        lower.matrix_parent_inverse == before)

H.section("[5] it exports as ordinary parts")
root.scale = (1.0, 1.0, 1.0)
bpy.context.view_layer.update()
props.entity = coll
result = bpy.ops.pc2.export_entity()
H.check("exported", result == {'FINISHED'}, str(result))
text = open(H.out("entities_compound.js"), encoding="utf-8").read()
block = pc2_entity.entity_block(text, "COMPOUNDTEST")
print(block)
H.check("two hemisphere parts", block.count("mesh: 'mesh_hemisphere'") == 2)
H.check("the lower half is a parented half turn",
        "rot: [180, 0, 0]" in block and "parent: 0" in block)
H.check("and the blueprint gained no new field for it",
        "rigid" not in block and "compound" not in block)

H.dump_expected(pc2_entity, "expected_compound.json", [root, lower])
H.finish()
