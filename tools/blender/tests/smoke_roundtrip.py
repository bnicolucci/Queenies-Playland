"""entities.js -> Blender -> entities.js must be byte-identical.

Runs against the REAL src/entities.js (copied first, never written to),
with ROCKET's parent/rot/color chain as the fixture; the uv shapes are
checked as literals, since which entity carries one is free to change.
Also covers the entity list: editing one shows it alone, and editing one that
is already in the .blend switches rather than importing a second copy, and the
half-turn sign that a decomposition is free to flip either way.
"""
import os
import shutil
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2_entity = H.setup()

WORK = H.out("entities_roundtrip.js")
shutil.copyfile(H.ENTITIES, WORK)
props = bpy.context.scene.pc2_entity
props.entities_path = WORK
bpy.ops.pc2.load_primitives()

original = open(WORK, encoding="utf-8").read()
names = sorted(pc2_entity.existing_entity_names(original))
print("")
print("entities found: %s" % ", ".join(names))

H.section("[1] parse")
for name in names:
    parts = pc2_entity.parse_blueprint(original, name)
    H.check("parse %s" % name, isinstance(parts, list) and len(parts) > 0,
            "%d part(s)" % (len(parts) if parts else 0))
# Emission is checked against LITERALS, which depend on no content at all --
# all three uv shapes, where naming one entity covered only the shape it
# happened to carry. CAPSULE's tile was that fixture until every entity moved
# to a flat colour and the tile went away; same lesson as the atlas and the
# stage moods, one directory over.
for spec, text in (({"tile": {"u": 2, "v": 2}}, "{ tile: { u: 2, v: 2 } }"),
                   ({"repeatU": 3, "repeatV": 3}, "{ repeatU: 3, repeatV: 3 }"),
                   ({"rect": [0.25, 0.5, 0.125, 0.125]},
                    "{ rect: [0.25, 0.5, 0.125, 0.125] }")):
    H.check("uv re-emits identically: %s" % text,
            pc2_entity.uv_to_js(spec) == text, pc2_entity.uv_to_js(spec))

# And whatever uv the real file carries today must survive parsing -- found
# rather than named, so this keeps testing the file as it is authored.
carried = [(name, part["uv"]) for name in names
           for part in (pc2_entity.parse_blueprint(original, name) or [])
           if part.get("uv")]
H.check("the real file's uvs survive parsing",
        all(isinstance(uv, dict) and uv for _, uv in carried),
        ", ".join("%s %s" % (n, pc2_entity.uv_to_js(uv)) for n, uv in carried)
        or "no entity carries a uv today")

H.section("[2] import into Blender")
for name in names:
    result = bpy.ops.pc2.edit_entity(name=name)
    H.check("import %s" % name, result == {'FINISHED'}, str(result))
    coll = bpy.data.collections.get(name)
    H.check("%s collection built" % name, coll is not None and len(coll.all_objects) > 0)
    H.check("%s is the one entity in the scene" % name,
            [c.name for c in bpy.context.scene.collection.children
             if c.name in names] == [name],
            str([c.name for c in bpy.context.scene.collection.children]))

# editing one that is already here switches to it -- it must not import a
# second copy, which Blender would silently call NAME.001
before = len(bpy.data.collections)
result = bpy.ops.pc2.edit_entity(name=names[0])
H.check("re-editing an imported entity just switches to it",
        result == {'FINISHED'} and len(bpy.data.collections) == before
        and props.entity.name == names[0],
        "%s, %d -> %d collections" % (result, before, len(bpy.data.collections)))
# A linked sub-entity's preview collections are disposable caches that share
# one name (Blender suffixes the second), not entities that were renamed.
H.check("nothing was renamed to .001",
        not any(c.name.endswith(".001") for c in bpy.data.collections
                if pc2_entity.subentities.PREVIEW not in c))

H.section("[3] re-export and diff")
for name in names:
    props.entity = bpy.data.collections.get(name)
    result = bpy.ops.pc2.export_entity()
    H.check("export %s" % name, result == {'FINISHED'}, str(result))

final = open(WORK, encoding="utf-8").read()
for name in names:
    before = pc2_entity.entity_block(original, name)
    after = pc2_entity.entity_block(final, name)
    ok = before == after
    H.check("%s byte-identical" % name, ok)
    if not ok:
        print("       before:")
        print(before)
        print("       after:")
        print(after)

H.check("whole file byte-identical", final == original)
if final != original:
    import difflib
    for line in difflib.unified_diff(original.splitlines(), final.splitlines(),
                                     "before", "after", lineterm="", n=1):
        print("       " + line)

H.section("[4] half turns pin to +180")
# A decomposition may return either sign for a half turn -- Blender gives an X
# half turn as -180 and a Y half turn as +180 -- and both are the same matrix,
# so the round-trip above only holds if the export picks one and sticks to it.
H.check("half_turn(-180) is +180", pc2_entity.half_turn(-180.0) == 180.0)
H.check("half_turn(180) is left alone", pc2_entity.half_turn(180.0) == 180.0)
H.check("half_turn rounds before deciding", pc2_entity.half_turn(-179.99999) == 180.0)
H.check("half_turn leaves other angles alone", pc2_entity.half_turn(-90.0) == -90.0)

part = {}
pc2_entity.emit_trs(part, (0, 0, 0), (-180.0, 0.0, -180.0), (1, 1, 1))
H.check("emit_trs pins every rotation axis", part.get("rot") == ["180", "0", "180"], str(part))

# -180 is an ordinary position or scale; only rotation may be rewritten.
part = {}
pc2_entity.emit_trs(part, (-180.0, 0, 0), (0, 0, 0), (1, -180.0, 1))
H.check("-180 survives in pos and scale",
        part.get("pos") == ["-180", "0", "0"] and part.get("scale") == ["1", "-180", "1"],
        str(part))

H.finish()
