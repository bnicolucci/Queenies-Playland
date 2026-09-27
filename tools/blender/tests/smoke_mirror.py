"""Mirror Across X: copy a sub-assembly to the other side, positive scale only.

The operator exists because Blender's own mirroring reaches for a negative
scale, which this format would rather not carry -- so the interesting claims
are that the copy really is the mirror image (checked on the drawn VERTICES,
not just the matrices) and that nothing in it ends up scaled negative.

Also pins the mirror PLANE: it is the parent's x = 0, not the entity's, so the
body is rotated here on purpose -- mirroring about the entity origin would put
the copy somewhere else entirely and the last check says so.

No browser check: the operator emits ordinary TRS parts, and smoke_entity's
browser half already proves Blender's world matrices and spawnEntity's agree
for an arbitrary parented tree. What is new here is only where the copy lands.
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
props.entities_path = H.out("entities_mirror.js")
if os.path.exists(H.out("entities_mirror.js")):
    os.remove(H.out("entities_mirror.js"))

# The reflection in each space. conjugate() carries a linear map across the
# basis change the same way it carries a transform, so there is still exactly
# one definition of "the mirror" in the addon. For THIS axis the two come out
# equal -- C swaps y and z, which diag(-1,1,1) does not notice -- so do not
# read a passing test here as proof that the basis change was applied.
Q_ENGINE = pc2_entity.MIRROR_X
Q_BLENDER = pc2_entity.conjugate(Q_ENGINE)


def close(a, b, tol=1e-5):
    return all(abs(a[r][c] - b[r][c]) < tol for r in range(4) for c in range(4))


def engine(obj, local=False):
    return pc2_entity.conjugate(obj.matrix_local if local else obj.matrix_world)


def world_points(obj, through=None):
    """The drawn geometry: every mesh vertex in world space, optionally sent
    through `through` first (the mirror, for the comparison below)."""
    m = obj.matrix_world if through is None else through @ obj.matrix_world
    return sorted(tuple((round(v, 4) or 0.0) for v in (m @ vert.co))
                  for vert in obj.data.vertices)


def mirror_about(frame_blender):
    """The reflection in `frame`'s x = 0 plane, as a world-space transform."""
    return frame_blender @ Q_BLENDER @ frame_blender.inverted()


def add(mesh, name, parent=None, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    props.parent_to_active = parent is not None
    if parent is not None:
        bpy.context.view_layer.objects.active = parent
    bpy.ops.pc2.add_part(mesh_name=mesh)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.location = loc
    obj.rotation_euler = Euler([math.radians(v) for v in rot], 'XYZ')
    obj.scale = scale
    bpy.context.view_layer.update()
    return obj


bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name="MIRROR")

# A body the eye hangs off, turned so that "mirror about the parent" and
# "mirror about the entity origin" are different answers.
body = add("mesh_hemicylinder", "body", loc=(0.0, 0.2, 0.35), rot=(0.0, 0.0, 35.0))
eye = add("mesh_hemisphere", "eye", parent=body,
          loc=(-0.4, -0.3, 0.2), rot=(-25.0, 10.0, 40.0), scale=(0.25, 0.25, 0.25))
pupil = add("mesh_hemisphere", "pupil", parent=eye,
            loc=(0.02, 0.1, 0.05), scale=(0.5, 0.3, 0.5))
lash = add("mesh_horn", "lash", parent=eye,
           loc=(0.0, 0.05, 0.12), rot=(115.0, 0.0, 0.0), scale=(0.4, 0.9, 0.4))

assembly = [eye, pupil, lash]

H.section("[1] mirror the eye")
for obj in bpy.context.selected_objects:
    obj.select_set(False)
eye.select_set(True)
bpy.context.view_layer.objects.active = eye
result = bpy.ops.pc2.mirror_parts()
bpy.context.view_layer.update()
H.check("operator finished", result == {'FINISHED'}, str(result))

coll = bpy.data.collections.get("MIRROR")
parts = [o for o in coll.all_objects if pc2_entity.mesh_name_of(o)]
H.check("the children came along (4 parts -> 7)", len(parts) == 7,
        "%d: %s" % (len(parts), sorted(o.name for o in parts)))

copies = {obj: bpy.data.objects.get(obj.name + ".001") for obj in assembly}
H.check("every part of the assembly has a copy",
        all(dup is not None for dup in copies.values()),
        str({o.name: (d.name if d else None) for o, d in copies.items()}))

H.section("[2] the copy is the mirror image, in the parent's frame")
# The assembly hangs off `body`, so the mirror is body's x = 0 plane -- in
# world terms P * Q * P^-1, which is only Q itself when the parent sits at the
# origin unrotated. Section [5] is the other half of this: that the plane is
# NOT the entity's.
reflect = mirror_about(body.matrix_world)
for obj, dup in copies.items():
    H.check("%s: world matrix is reflected about the body" % obj.name,
            close(dup.matrix_world, reflect @ obj.matrix_world @ Q_BLENDER))
    # Matrices agreeing is not quite the claim -- the claim is that the SHAPE
    # drawn lands mirrored, which holds only because the primitive is symmetric
    # about its own local YZ plane and absorbs the leftover flip.
    H.check("%s: drawn vertices are the mirrored original" % obj.name,
            world_points(dup) == world_points(obj, through=reflect))

H.section("[3] nothing came out scaled negative")
for obj, dup in copies.items():
    scale = dup.matrix_world.decompose()[2]
    H.check("%s: world scale is positive" % dup.name,
            all(v > 0.0 for v in scale), str(list(scale)))
    _, _, part_scale, exact = pc2_entity.decompose_part(dup)
    H.check("%s: exported scale is positive" % dup.name,
            all(v > 0.0 for v in part_scale), str(part_scale))
    H.check("%s: local matrix has no shear" % dup.name, exact)

H.section("[4] the blueprint numbers are the documented rule")
# pos.x, rot.y and rot.z negated; scale untouched.
for obj, dup in copies.items():
    pos, rot, scale, _ = pc2_entity.decompose_part(obj)
    d_pos, d_rot, d_scale, _ = pc2_entity.decompose_part(dup)
    H.check("%s: pos.x negated, pos.y/z kept" % obj.name,
            abs(d_pos[0] + pos[0]) < 1e-4 and abs(d_pos[1] - pos[1]) < 1e-4
            and abs(d_pos[2] - pos[2]) < 1e-4,
            "%s -> %s" % (pos, d_pos))
    H.check("%s: rot.x kept, rot.y/z negated" % obj.name,
            abs(d_rot[0] - rot[0]) < 1e-3 and abs(d_rot[1] + rot[1]) < 1e-3
            and abs(d_rot[2] + rot[2]) < 1e-3,
            "%s -> %s" % (rot, d_rot))
    H.check("%s: scale untouched" % obj.name,
            all(abs(a - b) < 1e-4 for a, b in zip(scale, d_scale)),
            "%s -> %s" % (scale, d_scale))

H.section("[5] the mirror plane is the PARENT's, not the entity's")
# eye hangs off a body that is rotated 35 deg, so mirroring about the entity
# origin would land the copy somewhere else. If this ever stops being true the
# test above still passes on a wrong plane, so check it explicitly.
H.check("local matrix is Q * L * Q",
        close(engine(copies[eye], local=True),
              Q_ENGINE @ engine(eye, local=True) @ Q_ENGINE))
H.check("and that is NOT the entity-origin mirror",
        not close(engine(copies[eye]), Q_ENGINE @ engine(eye) @ Q_ENGINE, tol=1e-3),
        "body is turned %.1f deg, so the two planes disagree"
        % math.degrees(body.rotation_euler.z))

H.section("[6] export")
props.entity = coll
result = bpy.ops.pc2.export_entity()
H.check("operator finished", result == {'FINISHED'}, str(result))
text = open(H.out("entities_mirror.js"), encoding="utf-8").read()
print(text)
H.check("no negative scale anywhere in the blueprint", "scale: [-" not in text)
blueprint = pc2_entity.parse_blueprint(text, "MIRROR")
H.check("all 7 parts exported", len(blueprint) == 7, str(len(blueprint)))

H.finish()
