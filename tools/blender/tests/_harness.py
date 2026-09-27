"""Shared rig for the Blender-side tests.

Run them all with `bun run test:blender`, or one at a time:
    blender --background --factory-startup --python tools/blender/tests/smoke_uv.py

Needs the "PicoCad2 Blender Tools" addon installed (the tests enable it), and
writes only to a scratch directory -- src/entities.js is never touched.
"""
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.environ.get("PC2_TEST_OUT") or os.path.join(HERE, ".out")
MODEL = os.path.join(REPO, "src", "assets", "model.txt")   # junction: picoCAD2's own dir
ENTITIES = os.path.join(REPO, "src", "entities.js")

os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "tools", "blender"))

_fails = []


def setup():
    """Enable both addons and return the module under test."""
    bpy.ops.preferences.addon_enable(module="picocad2_blender_tools")
    import pc2_entity
    pc2_entity.register()
    props = bpy.context.scene.pc2_entity
    props.model_path = MODEL
    return pc2_entity


def check(label, ok, detail=""):
    print(("  OK   " if ok else "  FAIL ") + label + ("   " + detail if detail else ""))
    if not ok:
        _fails.append(label)


def out(name):
    return os.path.join(OUT, name)


def section(title):
    print("")
    print(title)


def dump_expected(pc2_entity, name, objs):
    """Engine-space world matrices for the browser check, in DOMMatrix's
    column-major toFloat32Array order."""
    matrices = []
    for obj in objs:
        m = pc2_entity.C @ obj.matrix_world @ pc2_entity.C
        matrices.append([m[row][col] for col in range(4) for row in range(4)])
    with open(out(name), "w", encoding="utf-8") as handle:
        json.dump({"names": [o.name for o in objs], "matrices": matrices},
                  handle, indent=1)


def finish():
    print("")
    print("%s  (%d failure(s))"
          % ("ALL PASS" if not _fails else "FAILURES: " + ", ".join(_fails), len(_fails)))
    sys.exit(1 if _fails else 0)
