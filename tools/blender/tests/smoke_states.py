"""Pose states: Blender authoring, compact export, UID remapping and guards."""
import json
import math
import os
import shutil
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()
WORK = H.out("entities_states.js")
shutil.copyfile(H.ENTITIES, WORK)
props = bpy.context.scene.pc2_entity
props.entities_path = WORK
bpy.ops.pc2.load_primitives()
bpy.ops.pc2.edit_entity(name="UNICORN")
coll = props.entity


def tween_matrices(blueprint, states, t):
    by_state = [{int(delta[0]): delta for delta in state[1]} for state in states]
    locals_ = []
    for i, part in enumerate(blueprint):
        base = pc2_entity.blueprint_trs(part)
        values = []
        for field in range(1, 4):
            a = by_state[0].get(i)
            b = by_state[1].get(i)
            start = a[field] if a and a[field] else base[field - 1]
            end = b[field] if b and b[field] else base[field - 1]
            values.append([x + (y - x) * t for x, y in zip(start, end)])
        local = pc2_entity.state_local(part, [i] + values)
        if "parent" in part:
            local = locals_[int(part["parent"])] @ local
        locals_.append(local)
    return [[m[row][col] for col in range(4) for row in range(4)] for m in locals_]


def dump_expected(name, objects, blueprint, states):
    matrices = []
    for obj in objects:
        m = pc2_entity.C @ obj.matrix_world @ pc2_entity.C
        matrices.append([m[row][col] for col in range(4) for row in range(4)])
    with open(H.out(name), "w", encoding="utf-8") as handle:
        json.dump({
            "names": [o.name for o in objects],
            "matrices": matrices,
            "visible": [not o.hide_get() for o in objects],
            "tween_matrices": tween_matrices(blueprint, states, 0.5),
        }, handle, indent=1)


def by_uid(text, objects):
    """Each state's mask and overrides keyed by part UID.

    Runtime states address parts by INDEX, so a reorder is supposed to move
    every index and change nothing else. Comparing UID-keyed maps says exactly
    that, where naming a mesh and a slot only says it about one entity.
    """
    states = pc2_entity.parse_states(text, "UNICORN")
    uids = [pc2_entity.part_uid(o) for o in objects]
    return [({uids[i]: bool(int(state[0]) >> i & 1) for i in range(len(uids))},
             {uids[int(d[0])]: d[1:] for d in state[1]}) for state in states]


def drawable(coll, skip=()):
    """Any mesh part -- the suite needs a part to poke, not a named one."""
    return next(o for o in pc2_entity.ordered_parts(coll)
                if pc2_entity.mesh_name_of(o) is not None and o not in skip)


def refused(call):
    """Blender may surface an ERROR report as RuntimeError in background mode."""
    try:
        return call() == {'CANCELLED'}
    except RuntimeError:
        return True


H.section("[1] imported state data")
data = pc2_entity.state_data(coll)
order = pc2_entity.ordered_parts(coll)
H.check("two states imported", len(data["states"]) == 2, str(data["states"]))
H.check("base pose is active", data["active"] == "")
uids = [pc2_entity.part_uid(o) for o in order]
H.check("every part has one unique authoring UID", len(set(uids)) == len(order))

calm = data["states"][0]["name"]
spooked = data["states"][1]["name"]
horn = next(o for o in order if pc2_entity.mesh_name_of(o) == "mesh_horn")

H.section("[2] apply states and protect the base blueprint")
result = bpy.ops.pc2.apply_state(name=calm)
H.check("calm applies", result == {'FINISHED'}, str(result))
H.check("calm hides the horn", horn.hide_get())
result = bpy.ops.pc2.apply_state(name=spooked)
H.check("spooked applies", result == {'FINISHED'}, str(result))
H.check("spooked shows the horn", not horn.hide_get())
H.check("export refuses an applied state", refused(lambda: bpy.ops.pc2.export_entity()))

result = bpy.ops.pc2.apply_base()
H.check("base pose restores", result == {'FINISHED'} and not horn.hide_get(), str(result))
before = open(WORK, encoding="utf-8").read()
result = bpy.ops.pc2.export_entity()
after = open(WORK, encoding="utf-8").read()
H.check("stateful export finishes", result == {'FINISHED'}, str(result))
H.check("import/export is byte-identical", before == after)

H.section("[3] same-count reorder remaps indices through UIDs")
# Move the last root ahead of the body subtree without adding/removing parts.
# A count-only guard cannot detect this; UID-backed snapshots can.
before_text = open(WORK, encoding="utf-8").read()
before_blueprint = pc2_entity.parse_blueprint(before_text, "UNICORN")
before_maps = by_uid(before_text, pc2_entity.ordered_parts(coll))
horn_before = next(i for i, part in enumerate(before_blueprint)
                   if part.get("mesh") == "mesh_horn")
members = set(order)
last_root = [o for o in order if o.parent not in members][-1]
last_root[pc2_entity.ORDER_PROP] = -1
result = bpy.ops.pc2.export_entity()
H.check("reordered stateful export finishes", result == {'FINISHED'}, str(result))
text = open(WORK, encoding="utf-8").read()
blueprint = pc2_entity.parse_blueprint(text, "UNICORN")
states = pc2_entity.parse_states(text, "UNICORN")
horn_index = next(i for i, part in enumerate(blueprint) if part.get("mesh") == "mesh_horn")
H.check("horn moved to a new runtime slot", horn_index != horn_before,
        "%s -> %s" % (horn_before, horn_index))
H.check("calm mask followed the horn", not (states[0][0] >> horn_index & 1))
H.check("every mask and override followed its part",
        by_uid(text, pc2_entity.ordered_parts(coll)) == before_maps)
H.section("[4] structural and transform guards")
props.parent_to_active = False
bpy.ops.pc2.add_part(mesh_name="mesh_cube")
added = bpy.context.view_layer.objects.active
H.check("an older state refuses a newly added part",
        refused(lambda: bpy.ops.pc2.export_entity()))
bpy.data.objects.remove(added, do_unlink=True)

bpy.ops.pc2.apply_base()
bpy.ops.pc2.new_state(name="SCALED_ROTATION")
horn.rotation_euler.x += math.radians(15)
bpy.context.view_layer.update()
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
result = bpy.ops.pc2.export_entity()
scaled_states = pc2_entity.parse_states(open(WORK, encoding="utf-8").read(), "UNICORN")
scaled_delta = next((delta for delta in scaled_states[-1][1]
                     if delta[0] == horn_index), None)
H.check("a non-uniformly scaled part can rotate in a state",
        result == {'FINISHED'} and scaled_delta and scaled_delta[2],
        str(scaled_states[-1]))
# The browser check consumes this exported state, proving the direct rotation
# survives all the way through the real runtime rather than merely exporting.
bpy.ops.pc2.apply_state(name="SCALED_ROTATION")
dump_expected("expected_states.json", pc2_entity.ordered_parts(coll),
              blueprint, scaled_states)
bpy.ops.pc2.apply_base()

# An actual sheared snapshot still has no representation in the engine's TRS
# vocabulary. Corrupt a stored matrix directly because Blender's object
# position/rotation/scale controls cannot create this matrix by themselves.
bpy.ops.pc2.new_state(name="BAD_SHEAR")
bpy.ops.pc2.apply_base()
data = pc2_entity.state_data(coll)
saved = next(s for s in data["states"] if s["name"] == "BAD_SHEAR")
item = saved["parts"][pc2_entity.part_uid(horn)]
sheared = pc2_entity.matrix_from_values(item["m"])
sheared[0][1] += 0.25
item["m"] = pc2_entity.matrix_values(sheared)
item["d"] = True   # a hand-corrupted entry is a POSED one; unposed parts resolve to the base
pc2_entity.save_state_data(coll, data)
H.check("an actual sheared state is refused",
        refused(lambda: bpy.ops.pc2.export_entity()))
bpy.ops.pc2.delete_state(name="BAD_SHEAR")

bpy.ops.pc2.new_state(name="BAD_SCALE")
body = drawable(coll)
body.scale.x = -abs(body.scale.x)
bpy.context.view_layer.update()
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
H.check("negative state scale is refused",
        refused(lambda: bpy.ops.pc2.export_entity()))
bpy.ops.pc2.delete_state(name="BAD_SCALE")

H.section("[5] deleting all states resets first-state base capture")
data = pc2_entity.state_data(coll)
data["states"] = []
data["active"] = ""
old_base = data["base"]
pc2_entity.save_state_data(coll, data)
body.location.x += 0.25
bpy.context.view_layer.update()
fresh_base = pc2_entity.entity_snapshot(coll)
result = bpy.ops.pc2.new_state(name="FRESH")
data = pc2_entity.state_data(coll)
H.check("new first state replaces the stale base",
        result == {'FINISHED'} and data["base"] == fresh_base
        and data["base"] != old_base)

H.section("[6] hierarchy changes are rebased, not lost")
# Export somewhere else from here on: expected_states.json was dumped against
# the file as it stood above, and the browser check reads both.
SYNC_WORK = H.out("entities_states_sync.js")
shutil.copyfile(WORK, SYNC_WORK)
props.entities_path = SYNC_WORK
# Re-parenting keeps every UID, so nothing goes missing -- what moves is the
# frame a stored LOCAL matrix means anything in. Check the WORLD pose, since
# that is what was posed and the only thing worth preserving.
bpy.ops.pc2.apply_base()
order = pc2_entity.ordered_parts(coll)
members = set(order)
body = next(o for o in order if pc2_entity.mesh_name_of(o) is not None
            and o.parent not in members)
mover = next(o for o in order if o is not body and o.parent is not body
             and o not in body.children_recursive
             and pc2_entity.mesh_name_of(o) is not None)

bpy.ops.pc2.new_state(name="POSED")
mover.location.z += 0.31
mover.rotation_euler.y += math.radians(24)
bpy.context.view_layer.update()
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()


def state_world(name, obj):
    """The part's world matrix with a state applied, read from Blender."""
    bpy.ops.pc2.apply_state(name=name)
    bpy.context.view_layer.update()
    m = obj.matrix_world.copy()
    bpy.ops.pc2.apply_base()
    return m


def max_delta(a, b):
    return max(abs(x - y) for ra, rb in zip(a, b) for x, y in zip(ra, rb))


posed_world = state_world("POSED", mover)

# Re-parent the way Blender does, keeping the world transform.
world = mover.matrix_world.copy()
mover.parent = body
mover.matrix_parent_inverse = body.matrix_world.inverted()
mover.matrix_world = world
bpy.context.view_layer.update()

moved, added, removed = pc2_entity.states_drift(pc2_entity.state_data(coll),
                                                pc2_entity.ordered_parts(coll))
H.check("a re-parent is detected", bool(moved) and not added and not removed,
        "%s %s %s" % (moved, added, removed))
H.check("export refuses until synced", refused(lambda: bpy.ops.pc2.export_entity()))
H.check("sync refuses from an applied state",
        refused(lambda: (bpy.ops.pc2.apply_state(name="POSED"), bpy.ops.pc2.sync_states())[1]))
bpy.ops.pc2.apply_base()

result = bpy.ops.pc2.sync_states()
H.check("sync finishes", result == {'FINISHED'}, str(result))
delta = max_delta(posed_world, state_world("POSED", mover))
H.check("the state keeps the part's world pose across a re-parent", delta < 1e-5,
        "max component delta %g" % delta)
H.check("no drift remains", not any(pc2_entity.states_drift(
    pc2_entity.state_data(coll), pc2_entity.ordered_parts(coll))))
H.check("re-parented export finishes",
        bpy.ops.pc2.export_entity() == {'FINISHED'})

H.section("[7] added and removed parts")
props.parent_to_active = False
bpy.ops.pc2.add_part(mesh_name=pc2_entity.mesh_name_of(body))
extra = bpy.context.view_layer.objects.active
extra.location.x += 1.5
bpy.context.view_layer.update()
H.check("an added part is detected", bool(pc2_entity.states_drift(
    pc2_entity.state_data(coll), pc2_entity.ordered_parts(coll))[1]))
H.check("export still refuses", refused(lambda: bpy.ops.pc2.export_entity()))
base_world = extra.matrix_world.copy()
H.check("sync adopts it", bpy.ops.pc2.sync_states() == {'FINISHED'})
delta = max_delta(base_world, state_world("POSED", extra))
H.check("an older state leaves the new part in its base pose", delta < 1e-5,
        "max component delta %g" % delta)
H.check("export finishes with the added part",
        bpy.ops.pc2.export_entity() == {'FINISHED'})

bpy.data.objects.remove(extra, do_unlink=True)
bpy.context.view_layer.update()
H.check("a removed part is detected", bool(pc2_entity.states_drift(
    pc2_entity.state_data(coll), pc2_entity.ordered_parts(coll))[2]))
H.check("sync drops it", bpy.ops.pc2.sync_states() == {'FINISHED'})
H.check("export finishes after the removal",
        bpy.ops.pc2.export_entity() == {'FINISHED'})
H.check("sync is a no-op once in step",
        bpy.ops.pc2.sync_states() == {'FINISHED'})

H.section("[8] snapshots written before parent tracking")
# A snapshot with no recorded parent can only assume the tree has not moved
# since -- true exactly once, before the first re-parent. Opening the .blend is
# where that assumption is cashed in, so simulate a legacy .blend here.
bpy.ops.pc2.apply_base()
data = pc2_entity.state_data(coll)
for snapshot in [data["base"]] + [saved["parts"] for saved in data["states"]]:
    for item in snapshot.values():
        item.pop("p", None)
pc2_entity.save_state_data(coll, data)
H.check("a legacy snapshot shows no drift on its own",
        not any(pc2_entity.states_drift(pc2_entity.state_data(coll),
                                        pc2_entity.ordered_parts(coll))))
# ...which is a GUESS, not an answer, so both operators refuse rather than
# act on it -- the one thing that must not happen is a silent no-op here.
H.check("unstamped states are recognised",
        pc2_entity.states_unstamped(pc2_entity.state_data(coll)))
H.check("sync refuses unstamped states", refused(lambda: bpy.ops.pc2.sync_states()))
H.check("export refuses unstamped states", refused(lambda: bpy.ops.pc2.export_entity()))
pc2_entity.stamp_state_parents()
data = pc2_entity.state_data(coll)
H.check("load stamps the tree into every legacy snapshot",
        all("p" in item for saved in data["states"]
            for item in saved["parts"].values())
        and all("p" in item for item in data["base"].values()))

order = pc2_entity.ordered_parts(coll)
members = set(order)
root = next(o for o in order if o.parent not in members
            and pc2_entity.mesh_name_of(o) is not None)
child = next(o for o in order if o is not root and o.parent is not root
             and o not in root.children_recursive
             and pc2_entity.mesh_name_of(o) is not None)
world = child.matrix_world.copy()
child.parent = root
child.matrix_parent_inverse = root.matrix_world.inverted()
child.matrix_world = world
bpy.context.view_layer.update()
H.check("a re-parent after stamping is detected",
        bool(pc2_entity.states_drift(pc2_entity.state_data(coll),
                                     pc2_entity.ordered_parts(coll))[0]))
H.check("and syncs", bpy.ops.pc2.sync_states() == {'FINISHED'})
H.check("export finishes", bpy.ops.pc2.export_entity() == {'FINISHED'})

H.finish()
