"""stages.js -> Blender -> stages.js must be byte-identical.

Runs against COPIES of the real src/stages.js and src/entities.js, so the
shipped levels are the fixtures and nothing writes into the repo. Also covers
the two things a stage adds over an entity: a placement is an Empty instancing
an entity's collection (so the basis change lands on a matrix that is not a
part's), and the module is MERGED -- exporting one level may not disturb
another, the header, or the import line.
"""
import json
import math
import os
import re
import shutil
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2 = H.setup()

WORK = H.out("stages_roundtrip.js")
ENTS = H.out("entities_for_stages.js")
shutil.copyfile(os.path.join(H.REPO, "src", "stages.js"), WORK)
shutil.copyfile(H.ENTITIES, ENTS)
# The shipped stages are content and free to stop using banks, so the fixture
# AUTHORS one: the first placement of the first block gets `b: 2`, written
# where stage_block would write it (after everything else), so the round-trip
# below still has to come back byte-identical.
_stages = open(WORK, encoding="utf-8").read()
_stages = re.sub(r"^(  \{ e: [^\n]*?) \},$", r"\1, b: 2 },", _stages, count=1, flags=re.M)
open(WORK, "w", encoding="utf-8", newline="\n").write(_stages)
# main.js sits beside stages.js and is where the per-stage mood palettes
# live, so the scratch dir needs it for the palette checks below. Same rule:
# whether the game currently authors a mood or a bank is content, so the copy
# is given one mood and two banks -- one a reference into PALETTES, one a
# literal carrying its own shade ramp (128 chars) -- and the scan is tested
# against those.
shutil.copyfile(os.path.join(H.REPO, "src", "main.js"), H.out("main.js"))
_main = open(H.out("main.js"), encoding="utf-8").read()
_mood = "".join("%02x%02x%02x" % (i * 17, 255 - i * 17, 128) for i in range(16))
_main = pc2.PALETTES_LIST_RE.sub("const PALETTES = [\n  '',\n  '%s',\n];" % _mood, _main)
_main = pc2.BANKS_LIST_RE.sub("const PALETTE_BANKS = [PALETTES[1], '%s'];"
                              % (_mood[::-1] + "0123456789abcdef" * 2), _main)
open(H.out("main.js"), "w", encoding="utf-8", newline="\n").write(_main)
# main.js as it was BEFORE palette banks existed: the two halves of the scan
# have to be independent, so one missing array may not cost the other.
open(H.out("main_nobanks.js"), "w", encoding="utf-8").write(
    pc2.BANKS_LIST_RE.sub("", _main))
props = bpy.context.scene.pc2_entity
props.stages_path = WORK
props.entities_path = ENTS
bpy.ops.pc2.load_primitives()

original = open(WORK, encoding="utf-8").read()
names = sorted(pc2.existing_entity_names(original))
print("")
print("stages found: %s" % ", ".join(names))

H.section("[1] parse")
for name in names:
    entries = pc2.parse_stage(original, name)
    H.check("parse %s" % name, isinstance(entries, list) and len(entries) > 0,
            "%d placement(s)" % (len(entries) if entries else 0))
    H.check("%s entries name an entity" % name,
            all(isinstance(e.get("e"), str) for e in (entries or [])))

H.section("[2] import into Blender")
for name in names:
    result = bpy.ops.pc2.edit_stage(name=name)
    H.check("import %s" % name, result == {'FINISHED'}, str(result))
    coll = bpy.data.collections.get(name)
    entries = pc2.parse_stage(original, name)
    placed = pc2.placements_of(coll)
    H.check("%s has one placement per entry" % name,
            coll is not None and len(placed) == len(entries),
            "%d vs %d" % (len(placed), len(entries)))
    H.check("%s is the only stage in the scene" % name,
            [c.name for c in bpy.context.scene.collection.children
             if c.name in names] == [name],
            str([c.name for c in bpy.context.scene.collection.children]))
    # An entity linked into the scene next to its own instances draws one
    # extra copy of everything at the origin.
    sources = {o.instance_collection.name for o in placed}
    H.check("%s's entities are out of the scene" % name,
            not (sources & {c.name for c in bpy.context.scene.collection.children}),
            str(sorted(sources)))

H.section("[3] placements land where the entry says")
stage = names[0]
bpy.ops.pc2.edit_stage(name=stage)
entries = pc2.parse_stage(original, stage)
for entry, obj in zip(entries, pc2.placements_of(bpy.data.collections[stage])):
    pos, rot, scale, exact = pc2.decompose_matrix(pc2.conjugate(pc2.placement_matrix(obj)))
    got = {}
    pc2.emit_trs(got, pos, rot, scale)
    # Compared as the strings that get WRITTEN, not as floats: a decomposition
    # is free to return either sign for a half turn, and emit_trs is what pins
    # it -- comparing -180 against +180 as numbers would fail on two matrices
    # that are identical.
    want = {k: [pc2.num(pc2.half_turn(v)) if k == "rot" else pc2.num(v)
                for v in entry[short]]
            for short, k in (("p", "pos"), ("r", "rot"), ("s", "scale")) if short in entry}
    ok = exact and got == want
    H.check("%s %s round-trips its transform" % (stage, entry["e"]), ok,
            "" if ok else "want %s got %s" % (want, got))

H.section("[4] a placement is not a pivot")
sample = pc2.placements_of(bpy.data.collections[stage])[0]
H.check("is_placement says yes", pc2.is_placement(sample))
# Both are Empties. If is_pivot claimed one, dropping a placement into an
# entity collection would export a whole level as a hinge.
H.check("is_pivot says no", not pc2.is_pivot(sample))
H.check("is_part says no", not pc2.is_part(sample))

H.section("[4b] a previewed palette, as the game writes one")
# The dropdown is a preview; this is the one step that turns it into something
# main.js can hold. The invariant is that the string parses back to the colours
# that were on screen, or the game draws something other than what was shown.
MAIN = H.out("main.js")
lospec = pc2.PALETTE_LIBRARY[0]
for choice in (pc2.PALETTE_MODEL, lospec["id"]):
    props.palette = choice
    text = pc2.palette_hex(pc2.scene_palette(bpy.context))
    H.check("%s serialises to 96 hex chars" % choice, len(text) == 96, "%d" % len(text))
# A Lospec palette is published as 0..255 ints, so its string must be those
# bytes exactly -- no drift through the 0..1 floats the swatches speak.
H.check("a library palette serialises to its published bytes",
        pc2.palette_hex(pc2.PALETTE_COLORS[lospec["id"]])
        == "".join("%02x%02x%02x" % rgb for rgb in lospec["colors"]))
# And a stage mood read back out of main.js has to survive the same trip.
for stage, colors in sorted(pc2.game_palettes(MAIN).items()):
    H.check("%s's mood re-serialises" % stage,
            len(pc2.palette_hex(colors)) == 96)
props.palette = lospec["id"]
H.check("copy_palette finishes", bpy.ops.pc2.copy_palette() == {'FINISHED'})
# Headless Blender has no clipboard -- it reads back empty -- so the copy
# itself is exercised by hand; what is checkable here is what it would copy.
clip = bpy.context.window_manager.clipboard
H.check("it copies the palette on screen (headless: no clipboard to read back)",
        clip in ("", pc2.palette_hex(pc2.scene_palette(bpy.context))),
        (clip[:24] + "...") if clip else "empty, as expected headless")
H.section("[4b] palette banks")
# A bank is per-PLACEMENT, so it is the one thing that lets two copies of one
# blueprint wear different colours. Absent/0 must stay absent, or a stage
# authored before banks existed would come back a different file.
banked = [(n, e) for n in names for e in (pc2.parse_stage(original, n) or [])
          if e.get("b")]
H.check("a shipped stage authors a bank", bool(banked),
        ", ".join("%s -> b: %s" % (n, e["b"]) for n, e in banked) or "none found")
for name, entry in banked:
    coll = bpy.data.collections.get(name)
    hit = [o for o in pc2.placements_of(coll)
           if o.instance_collection.name == entry["e"] and o.get(pc2.BANK_PROP)]
    H.check("%s imports b: %s onto a placement" % (name, entry["b"]),
            any(int(o[pc2.BANK_PROP]) == int(entry["b"]) for o in hit),
            "%d placement(s) carry a bank" % len(hit))
for name in names:
    coll = bpy.data.collections.get(name)
    unbanked = [o for o in pc2.placements_of(coll) if not o.get(pc2.BANK_PROP)]
    H.check("%s leaves bank-0 placements unmarked" % name,
            all(pc2.BANK_PROP not in o for o in unbanked),
            "%d of %d" % (len(unbanked), len(pc2.placements_of(coll))))
# The operator is the authoring path, and round-tripping it is what the export
# half above proves; here we only check it writes and clears the property.
sample = pc2.placements_of(bpy.data.collections[names[0]])[0]
bpy.context.view_layer.objects.active = sample
bpy.ops.object.select_all(action='DESELECT')
sample.select_set(True)
bpy.ops.pc2.set_bank(index=2)
H.check("set_bank writes the property", int(sample.get(pc2.BANK_PROP, 0)) == 2)
bpy.ops.pc2.set_bank(index=0)
H.check("bank 0 REMOVES it rather than writing 0", pc2.BANK_PROP not in sample)
# How many banks the picker offers is scanned out of main.js, never typed.
MAIN = H.out("main.js")
H.check("game_banks reads main.js", pc2.game_banks(MAIN) >= 2,
        "%d bank(s)" % pc2.game_banks(MAIN))
H.check("game_banks fails soft on a missing main.js",
        pc2.game_banks(H.out("no_such_main.js")) == 1)

# The dropdown previews a bank, so the scan has to hand back COLOURS, not just
# a count -- and a bank entry is normally a reference into PALETTES rather than
# a literal, which is the part that can silently resolve to nothing.
banks = pc2.game_bank_palettes(MAIN)
H.check("bank palettes resolve to 16 colours",
        bool(banks) and all(b is None or len(b) == 16 for b in banks),
        "%d bank(s): %s" % (len(banks), [b is not None for b in banks]))
H.check("bank 1 resolves through its PALETTES reference", banks and banks[0] is not None)
# Scanning main.js may never break the moods it already read, and vice versa:
# main.js had no PALETTE_BANKS at all until banks landed.
H.check("moods still read from the same scan", bool(pc2.game_palettes(MAIN)),
        ", ".join(sorted(pc2.game_palettes(MAIN))))
H.check("a main.js with no PALETTE_BANKS still yields moods",
        bool(pc2.game_palettes(H.out("main_nobanks.js")))
        and pc2.game_bank_palettes(H.out("main_nobanks.js")) == [],
        "moods %d, banks %d" % (len(pc2.game_palettes(H.out("main_nobanks.js"))),
                                len(pc2.game_bank_palettes(H.out("main_nobanks.js")))))

# Selecting a bank in the View panel must actually change what the swatches and
# the flat preview show -- otherwise the option is decoration.
props.palette = pc2.PALETTE_MODEL
model_colors = pc2.scene_palette(bpy.context)
props.palette = "BANK1"
bank_colors = pc2.scene_palette(bpy.context)
H.check("picking a bank changes scene_palette", bank_colors != model_colors)
H.check("and it is bank 1's own colours", bank_colors == banks[0])
props.palette = "BANK%d" % (pc2.PALETTE_BANK_SLOTS)
H.check("a bank main.js does not load falls back to the model",
        pc2.scene_palette(bpy.context) == model_colors)
props.palette = pc2.PALETTE_MODEL

H.section("[5] re-export and diff")
intro = bpy.data.collections['INTRO']
guide = next(o for o in intro.objects if o.get('pc2_sign_guide'))
H.check('INTRO has a visible sign guide', guide.type == 'MESH' and guide.show_in_front)
H.check('sign guide never exports', guide not in pc2.placements_of(intro))
# Even an entity instance used as a visual helper must be excluded by name.
helper = pc2.placements_of(intro)[0].copy()
helper.name = 'PLACEMENT Background reference'
intro.objects.link(helper)
H.check('named entity helper never exports', helper not in pc2.placements_of(intro))
guide.name = 'Renamed sign reference'
H.check('renaming the generated guide keeps it excluded', pc2.is_stage_guide(guide))

for name in names:
    props.stage = bpy.data.collections.get(name)
    result = bpy.ops.pc2.export_stage()
    H.check("export %s" % name, result == {'FINISHED'}, str(result))

final = open(WORK, encoding="utf-8").read()
for name in names:
    before = pc2.entity_block(original, name)
    after = pc2.entity_block(final, name)
    ok = before == after
    H.check("%s byte-identical" % name, ok)
    if not ok:
        import difflib
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(),
                                         "before", "after", lineterm="", n=1):
            print("       " + line)

H.check("whole file byte-identical", final == original)
if final != original:
    import difflib
    for line in difflib.unified_diff(original.splitlines(), final.splitlines(),
                                     "before", "after", lineterm="", n=1):
        print("       " + line)

H.section("[6] a new stage, placed by hand")
bpy.context.scene.cursor.location = (1.0, 2.0, 3.0)
result = bpy.ops.pc2.new_stage(name="STAGE_TEST")
H.check("new stage", result == {'FINISHED'} and props.stage.name == "STAGE_TEST", str(result))
try:
    # bpy.ops turns an operator's ERROR report into a RuntimeError, so a
    # refusal is caught rather than returned.
    refused = bpy.ops.pc2.export_stage() == {'CANCELLED'}
except RuntimeError:
    refused = True
H.check("exporting an empty stage is refused", refused)

result = bpy.ops.pc2.place_entity(name="CUBE")
placed = pc2.placements_of(props.stage)
H.check("place CUBE", result == {'FINISHED'} and len(placed) == 1, str(result))
bpy.ops.pc2.set_color(index=5)
H.check("the placement took the colour", pc2.part_color(placed[0]) == 5,
        str(pc2.part_color(placed[0])))

bpy.ops.pc2.export_stage()
merged = open(WORK, encoding="utf-8").read()
entry = (pc2.parse_stage(merged, "STAGE_TEST") or [{}])[0]
# Blender (x, y, z) -> engine (-x, z, y): the 3D cursor is the one place a
# placement's position is authored, so this is where that swap is checked.
H.check("placed at the cursor, in engine space",
        entry.get("e") == "CUBE" and [float(v) for v in entry.get("p", [])] == [-1.0, 3.0, 2.0],
        str(entry))
H.check("the colour came out as c", entry.get("c") == 5, str(entry))

H.section("[7] the module is merged, not rewritten")
for name in names:
    H.check("%s survived a foreign export" % name,
            pc2.entity_block(merged, name) == pc2.entity_block(original, name))
H.check("the header is kept verbatim",
        merged.startswith(original[:original.index("import {")]))
H.check("there is exactly one import line",
        merged.count("from './entities.js';") == 1)
line = next(ln for ln in merged.splitlines() if ln.startswith("import {"))
imported = {n.strip() for n in line[line.index("{") + 1:line.index("}")].split(",")}
placed = {e["e"] for block in list(names) + ["STAGE_TEST"]
          for e in pc2.parse_stage(merged, block)}
# The import line is the one thing that cannot be merged block by block, so a
# stage that places something the line forgot is a build that will not resolve.
H.check("every placed entity is imported", placed <= imported,
        str(sorted(placed - imported)))
H.check("nothing unused is imported", imported <= placed,
        str(sorted(imported - placed)))

H.section("[8] the file is the source of truth for the list")
H.check("STAGE_TEST is listed once", pc2.stage_names(bpy.context).count("STAGE_TEST") == 1,
        str(pc2.stage_names(bpy.context)))
H.check("every exported stage is listed",
        set(names) <= set(pc2.stage_names(bpy.context)))

H.section("[9] the game camera")
# The camera is AUTHORED, never derived: one shot for every level, and a level
# is composed to fit it. So the check is that Blender builds exactly the
# camera the fields say, with the engine's conventions, and that nothing
# about a stage -- its bounds, its cast, switching to another -- moves it.
bpy.ops.pc2.edit_stage(name="COLOR_CHOOSER")
props_ = bpy.context.scene.pc2_entity
result = bpy.ops.pc2.stage_camera(look=False)
H.check("operator finished", result == {'FINISHED'}, str(result))
cam = bpy.data.objects.get(pc2.CAMERA_NAME)
H.check("one camera exists", cam is not None and cam.type == 'CAMERA')
H.check("it is the scene camera", bpy.context.scene.camera is cam)
H.check("it is NOT in the stage collection -- a stage switch must not take it",
        cam.name not in props.stage.objects,
        str([o.name for o in props.stage.objects]))
H.check("and it is not a placement", not pc2.is_placement(cam))

H.check("vertical FOV matches perspective(fov, ...)",
        cam.data.sensor_fit == 'VERTICAL'
        and abs(math.degrees(cam.data.angle_y) - props_.cam_fov) < 1e-3,
        "%s %.4f" % (cam.data.sensor_fit, math.degrees(cam.data.angle_y)))
H.check("near plane is the engine's 0.1", abs(cam.data.clip_start - 0.1) < 1e-6,
        str(cam.data.clip_start))

at, dist, radius = pc2.stage_view(bpy.context)
H.check("far plane is dist + radius * 3, radius the runtime's constant 10",
        radius == 10.0 and abs(cam.data.clip_end - (dist + radius * 3)) < 1e-4,
        "%.4f vs %.4f" % (cam.data.clip_end, dist + radius * 3))
H.check("at and dist are the fields, not a measurement of the stage",
        list(at) == list(props_.cam_at) and dist == props_.cam_dist)

# Geometry, independent of how camera_world builds the matrix: the eye is
# `dist` away from `at`, and it looks straight at it.
eye = pc2.conjugate_point(cam.matrix_world.translation)
H.check("the eye is `dist` from `at`",
        abs((Vector(eye) - Vector(at)).length - dist) < 1e-4,
        "%.4f vs %.4f" % ((Vector(eye) - Vector(at)).length, dist))
forward = pc2.conjugate_point(cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0)))
to_target = (Vector(at) - Vector(eye)).normalized()
H.check("it looks AT `at` (Blender cameras face -Z, like the engine)",
        (Vector(forward).normalized() - to_target).length < 1e-4,
        "%s vs %s" % ([round(v, 3) for v in forward], [round(v, 3) for v in to_target]))

# pitch 0 means level with the target, and yaw is measured so that +90 puts the
# eye on -X. Both are facts about engine.js's orbitView, not about Blender.
H.check("pitch 0 keeps the eye level with the target",
        abs(props_.cam_pitch) > 1e-6 or abs(eye[1] - at[1]) < 1e-4,
        "eye y %.4f vs at y %.4f" % (eye[1], at[1]))

# Hand the browser everything it needs to re-derive this with the REAL
# engine.js, including the frame Blender is about to draw.
frame = [list(pc2.conjugate_point(cam.matrix_world @ v))
         for v in cam.data.view_frame(scene=bpy.context.scene)]
# One-sided, like camera_world: eye space is shared, only the world side
# converts. C is its own inverse, so C @ M_blender is M_engine.
view = (pc2.C @ cam.matrix_world).inverted()
with open(H.out("camera.json"), "w", encoding="utf-8") as handle:
    json.dump({
        "yaw": props_.cam_yaw, "pitch": props_.cam_pitch, "dist": dist, "at": at,
        "fov": props_.cam_fov, "aspect": props_.cam_res_x / props_.cam_res_y,
        "far": dist + radius * 3,
        "view": [view[row][col] for col in range(4) for row in range(4)],
        "frame": frame,
    }, handle, indent=1)
H.check("wrote camera.json for the browser check", os.path.exists(H.out("camera.json")))

# Switching stage keeps the ONE camera exactly where it was: a level is
# composed to fit the frame, never the frame to the level.
before = len([o for o in bpy.data.objects if o.type == 'CAMERA'])
was = cam.matrix_world.copy()
bpy.ops.pc2.edit_stage(name="STAGE_1")
H.check("still exactly one camera",
        len([o for o in bpy.data.objects if o.type == 'CAMERA']) == before, str(before))
H.check("and a stage switch did not move it",
        max(abs(a - b) for ra, rb in zip(was, cam.matrix_world) for a, b in zip(ra, rb)) < 1e-6)
bpy.ops.pc2.edit_stage(name="COLOR_CHOOSER")

H.section("[9b] whether the stage fits the camera")
bpy.ops.pc2.edit_stage(name="STAGE_1")
fit_dist = props_.cam_dist
shot, total, missing = pc2.stage_fit(bpy.context, props.stage)
H.check("the fit report counts every placement", total == len(pc2.placements_of(props.stage)),
        "%d of %d" % (total, len(pc2.placements_of(props.stage))))
H.check("and reports what is out of shot", shot + len(missing) == total,
        "%d in, %d out: %s" % (shot, len(missing), sorted(set(missing))))
# Pulling the eye right in has to push things out of frame; shoving it far back
# has to bring them in. Anything else means the frustum test is not testing.
props_.cam_dist = 1.0
near_shot, near_total, _ = pc2.stage_fit(bpy.context, props.stage)
props_.cam_dist = 200.0
far_shot, far_total, _ = pc2.stage_fit(bpy.context, props.stage)
H.check("close in, less fits; far back, all of it does",
        near_shot < far_shot and far_shot == far_total,
        "%d/%d close vs %d/%d far" % (near_shot, near_total, far_shot, far_total))

# The aspect buttons are the horizontal-or-vertical decision.
H.check("aspect preset", bpy.ops.pc2.camera_aspect(width=540, height=960) == {'FINISHED'})
H.check("a taller frame is a taller frame",
        props_.cam_res_x == 540 and props_.cam_res_y == 960
        and bpy.context.scene.render.resolution_y > bpy.context.scene.render.resolution_x,
        "%dx%d" % (bpy.context.scene.render.resolution_x,
                   bpy.context.scene.render.resolution_y))
# The shape has to reach the FIT TEST, not just the render size -- a letterbox
# narrow enough must push a ring of objects out sideways. 16:9 and 9:16 both
# happen to fit this stage whole, so neither would prove anything.
# Back off the 200 the depth check left behind first, or the frame is so far
# away that even a sliver of an aspect still holds the whole ring.
props_.cam_dist = fit_dist
bpy.ops.pc2.camera_aspect(width=96, height=960)
narrow = pc2.stage_fit(bpy.context, props.stage)[0]
bpy.ops.pc2.camera_aspect(width=960, height=540)
wide = pc2.stage_fit(bpy.context, props.stage)[0]
H.check("the frame's SHAPE reaches the fit test, not just the render size",
        narrow < wide, "narrow %d, wide %d" % (narrow, wide))

H.section("[9c] the camera object is the truth, and game_view.js is where it starts")
# The game has ONE camera, in src/game_view.js. A fresh .blend has to start on
# it, a drifted .blend has to be able to get back to it, and a camera the
# author moved BY HAND in the viewport has to be what Export writes -- or
# Blender and the game are two different pictures.
shipped = pc2.read_game_view(os.path.join(H.REPO, "src", "game_view.js"))
H.check("read GAME_VIEW out of src/game_view.js", shipped is not None
        and all(k in shipped for k in pc2.GAME_VIEW_KEYS), str(shipped))
rna = props_.bl_rna.properties
H.check("a fresh .blend starts on the game's frame",
        rna["cam_res_x"].default == int(shipped["width"])
        and rna["cam_res_y"].default == int(shipped["height"])
        and "cam_mode" not in rna,   # nothing to fit any more: there is no mode
        "%dx%d" % (rna["cam_res_x"].default, rna["cam_res_y"].default))
H.check("and on the game's camera",
        all(abs(a - b) < 1e-6 for a, b in zip(rna["cam_at"].default_array, shipped["at"]))
        and all(abs(rna["cam_" + k].default - shipped[k]) < 1e-6
                for k in ("yaw", "pitch", "dist", "fov")))

# Load: a .blend whose fields have drifted gets the game's camera back.
shutil.copyfile(os.path.join(H.REPO, "src", "game_view.js"), H.out("game_view.js"))
bpy.ops.pc2.edit_stage(name="COLOR_CHOOSER")
props_.cam_yaw, props_.cam_pitch, props_.cam_dist = 33.0, -4.0, 7.0
props_.cam_at = (9.0, 9.0, 9.0)
bpy.ops.pc2.camera_aspect(width=960, height=540)
H.check("drifted fields differ from game_view.js",
        not pc2.camera_matches_game_view(props_, shipped))
H.check("load", bpy.ops.pc2.load_game_view() == {'FINISHED'})
H.check("and the fields ARE the game's camera again",
        pc2.camera_matches_game_view(props_, shipped)
        and (props_.cam_res_x, props_.cam_res_y) == (int(shipped["width"]), int(shipped["height"])),
        "%s %.3f %.3f %dx%d" % ([round(v, 3) for v in props_.cam_at], props_.cam_yaw,
                                props_.cam_dist, props_.cam_res_x, props_.cam_res_y))

# The camera OBJECT is the truth. Move and turn it by hand (a global-Z turn is
# a yaw, a local-X turn is a pitch; neither is a roll), sync, and the fields
# must describe EXACTLY that camera -- the round trip is the whole check.
bpy.ops.pc2.stage_camera(look=False)
cam = bpy.data.objects[pc2.CAMERA_NAME]
H.check("aimed camera syncs to no change",
        pc2.sync_camera_fields(bpy.context) < 1e-6
        and abs(props_.cam_yaw - shipped["yaw"]) < 1e-6)
cam.matrix_world = pc2.Matrix.Rotation(math.radians(25), 4, 'Z') @ cam.matrix_world
cam.rotation_euler.rotate_axis('X', math.radians(-7))
cam.location += Vector((1.5, -2.0, 0.75))
bpy.context.view_layer.update()
roll = pc2.sync_camera_fields(bpy.context)
rebuilt = pc2.camera_world(props_.cam_yaw, props_.cam_pitch, props_.cam_dist, list(props_.cam_at))
H.check("fields follow the hand-moved camera",
        abs(props_.cam_yaw - shipped["yaw"]) > 1 and abs(props_.cam_pitch - shipped["pitch"]) > 1,
        "yaw %.2f pitch %.2f" % (props_.cam_yaw, props_.cam_pitch))
H.check("and describe EXACTLY that camera (no roll to lose)",
        roll < 1e-3 and max(abs(a - b) for ra, rb in zip(rebuilt, cam.matrix_world)
                            for a, b in zip(ra, rb)) < 1e-4,
        "roll %.4f" % roll)
H.check("the panel agrees the camera is not the game's yet",
        not pc2.camera_matches_game_view(props_, shipped))

# Roll is the one thing orbitView cannot do: reported, the AIM kept, and the
# Game Camera button straightens it without moving where it looks.
aimed = (props_.cam_yaw, props_.cam_pitch, tuple(props_.cam_at))
cam.rotation_euler.rotate_axis('Z', math.radians(20))
bpy.context.view_layer.update()
roll = pc2.sync_camera_fields(bpy.context)
H.check("a rolled camera reports its roll", abs(roll - 20) < 1e-3, "%.4f" % roll)
H.check("and keeps its aim",
        abs(props_.cam_yaw - aimed[0]) < 1e-3 and abs(props_.cam_pitch - aimed[1]) < 1e-3
        and all(abs(a - b) < 1e-4 for a, b in zip(props_.cam_at, aimed[2])))
bpy.ops.pc2.stage_camera(look=False)
H.check("Game Camera re-aims it straight, looking where it looked",
        pc2.camera_fields(cam, props_.cam_dist)[3] < 1e-3
        and abs(props_.cam_yaw - aimed[0]) < 1e-3)

# Export reads the camera object, not the last thing the timer saw.
cam.location += Vector((0.0, 0.0, 1.0))
bpy.context.view_layer.update()
want = pc2.camera_fields(cam, props_.cam_dist)
H.check("export", bpy.ops.pc2.export_stage() == {'FINISHED'})
written = pc2.read_game_view(H.out("game_view.js"))
H.check("Export Stage wrote the hand-moved camera to game_view.js",
        written is not None
        and all(abs(a - b) < 1e-3 for a, b in zip(written["at"], want[2]))
        and abs(written["yaw"] - want[0]) < 1e-3 and abs(written["pitch"] - want[1]) < 1e-3
        and written["width"] == props_.cam_res_x and written["height"] == props_.cam_res_y,
        str(written))
H.check("and now the panel says this IS the game's camera",
        pc2.camera_matches_game_view(props_, written))

bpy.ops.pc2.load_game_view()
bpy.ops.pc2.edit_stage(name="COLOR_CHOOSER")

H.section("[10] a stage's mood palette")
# The game swaps 16 colours per stage (loadStage -> E.setPalette), so seeing an
# entity under the palette its LEVEL wears is a different question from seeing
# it under the model's. The addon reads both lists straight out of main.js.
moods = pc2.game_palettes(pc2.main_js_path(props))
# WHICH stages have a mood is main.js's business and moves as levels come and
# go (f16227f dropped DEMO from STAGES), so name none of them here -- what this
# checks is that the pairing works and that a '' entry means "the model's own".
H.check("read the stage moods out of main.js", bool(moods), str(sorted(moods)))
H.check("COLOR_CHOOSER is not in them -- its PALETTES entry is ''", "COLOR_CHOOSER" not in moods)
mood_stage = sorted(moods)[0]
H.check("a mood is 16 rgb triples in 0..1",
        len(moods[mood_stage]) == 16
        and all(len(c) == 3 and all(0.0 <= v <= 1.0 for v in c)
                for c in moods[mood_stage]),
        "%s %s" % (mood_stage, moods[mood_stage][:2]))

model_colors = pc2.model_palette(bpy.path.abspath(props.model_path))
props.palette = pc2.PALETTE_STAGE
bpy.ops.pc2.edit_stage(name=mood_stage)
H.check("with a mood stage up, the swatches are that stage's mood",
        pc2.scene_palette(bpy.context) == moods[mood_stage], mood_stage)
bpy.ops.pc2.edit_stage(name="COLOR_CHOOSER")
H.check("with COLOR_CHOOSER up, they fall back to the model's own -- what loadStage does",
        pc2.scene_palette(bpy.context) == model_colors)
props.palette = pc2.PALETTE_MODEL
H.check("and the model option is unchanged",
        pc2.scene_palette(bpy.context) == model_colors)

# It is a scan of hand-written game code, so it has to fail soft rather than
# take the panel down with it.
broken = H.out("broken_main.js")
with open(broken, "w", encoding="utf-8") as handle:
    handle.write("// nothing here resembles a stage list\n")
H.check("an unreadable main.js yields no moods, not an exception",
        pc2.game_palettes(broken) == {})
H.check("and a missing one is the same",
        pc2.game_palettes(H.out("no_such_main.js")) == {})

H.section("[11] the panel draws")


class FakeLayout:
    """Enough UILayout to walk a draw() headlessly.

    A panel is the one part of an addon a --background run never executes, so a
    typo in it survives every other test and shows up as a broken sidebar.
    """
    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            if name in ("box", "column", "row", "grid_flow", "split"):
                return FakeLayout(self.log)
            return type("Op", (), {})()   # .operator(...).name = ... lands here
        return call


for label, setup in (("no stage picked", lambda: setattr(props, "stage", None)),
                     ("a stage picked", lambda: bpy.ops.pc2.edit_stage(name=stage)),
                     ("a placement selected", lambda: setattr(
                         bpy.context.view_layer.objects, "active",
                         pc2.placements_of(props.stage)[0]))):
    setup()
    log = []
    # Called unbound on a stub: Panel.layout is RNA-backed and cannot be
    # assigned on a real instance, and draw() only ever reaches for self.layout.
    panel = type("Stub", (), {"layout": FakeLayout(log)})()
    try:
        pc2.PC2_PT_stage.draw(panel, bpy.context)
        ok, detail = True, "%d layout call(s)" % len(log)
    except Exception as exc:
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    H.check("Stage panel draws with %s" % label, ok, detail)

# The View panel is shared by both sections, so it has to survive every palette
# mode -- including "this stage's mood" with no stage picked.
# The camera box runs the fit report and the roll check once a camera exists,
# and the placement list sits under Export -- draw it with all of that live.
log = []
panel = type("Stub", (), {"layout": FakeLayout(log)})()
try:
    pc2.PC2_PT_stage.draw(panel, bpy.context)
    ok, detail = True, "%d layout call(s)" % len(log)
except Exception as exc:
    ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
H.check("Stage panel draws with the camera and placement list", ok, detail)

for mode in (pc2.PALETTE_MODEL, pc2.PALETTE_STAGE, pc2.PALETTE_ITEMS[2][0]):
    props.palette = mode
    for stage_name in (mood_stage, None):  # a stage main.js still names
        if stage_name:
            bpy.ops.pc2.edit_stage(name=stage_name)
        else:
            props.stage = None
        log = []
        panel = type("Stub", (), {"layout": FakeLayout(log)})()
        try:
            pc2.PC2_PT_view.draw(panel, bpy.context)
            ok, detail = True, "%d layout call(s)" % len(log)
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        H.check("View panel draws: palette %s, stage %s" % (mode, stage_name), ok, detail)
props.palette = pc2.PALETTE_MODEL

# Getting a previewed palette INTO the game: palette_hex is the exact inverse
# of the reader above, which is the invariant that matters -- a string copied
# out of Blender has to parse back to the colours that were on screen, or the
# game draws something other than what was previewed.
for choice in (pc2.PALETTE_MODEL, "BANK1", pc2.PALETTE_LIBRARY[0]["id"]):
    props.palette = choice
    shown = pc2.scene_palette(bpy.context)
    text = pc2.palette_hex(shown)
    H.check("%s serialises to 96 hex chars" % choice, len(text) == 96, "%d" % len(text))
    back = pc2.unhex_palette(text)
    H.check("%s round-trips through the game's own parse" % choice,
            back is not None
            and all(abs(a - b) <= 1.0 / 255 for p, q in zip(shown, back) for a, b in zip(p, q)))
# A Lospec palette is published as 0..255 ints, so its string must be those
# bytes exactly -- no drift through the 0..1 floats the swatches speak.
lospec = pc2.PALETTE_LIBRARY[0]
H.check("a library palette serialises to its published bytes",
        pc2.palette_hex(pc2.PALETTE_COLORS[lospec["id"]])
        == "".join("%02x%02x%02x" % rgb for rgb in lospec["colors"]))
# The operator is what the button runs. Headless Blender has no clipboard --
# window_manager.clipboard reads back empty -- so the check is that the
# operator succeeds and that what it WOULD copy is the palette on screen; the
# copy itself is one assignment and is exercised by hand.
props.palette = lospec["id"]
H.check("copy_palette finishes", bpy.ops.pc2.copy_palette() == {'FINISHED'})
clip = bpy.context.window_manager.clipboard
want = pc2.palette_hex(pc2.scene_palette(bpy.context))
H.check("it copies the palette on screen (headless: no clipboard to read back)",
        clip in ("", want), (clip[:24] + "...") if clip else "empty, as expected headless")
props.palette = pc2.PALETTE_MODEL

H.finish()
