bl_info = {
    "name": "picoCAD2 Entity Tools (js13k)",
    "author": "js13k-2026",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > picoCAD2",
    "description": "Compose and pose entity blueprints; export src/entities.js",
    "category": "Object",
}

# picoCAD2 Entity Tools
# =====================
# Authoring side of src/entity.js's blueprints. A blueprint part is just a
# reference to one of model.txt's meshes plus a TRS:
#     { mesh: 'mesh_hemicylinder', pos, rot (degrees), scale, color?, parent? }
# so this addon never touches geometry -- it exports an outliner, not a model.
#
# Workflow: the primitive library loads itself out of model.txt the first time
# the panel is drawn -> pick an entity from the list, or New Entity -> click
# primitive buttons to add parts, which are linked duplicates sharing the
# library's mesh datablocks (Blender's linked duplicate IS the engine's
# instance) -> Export.
#
# Exactly ONE entity is in the scene at a time (see focus_entity): blueprints
# are all authored around the origin, so leaving the others linked stacks them
# inside each other and editing the second one is unreadable.
#
# Levels were authored here too until Sep 2026 -- a stage was a collection of
# placed entity instances, exported to src/stages.js. That half was removed:
# it was most of the addon's surface for a job that is a handful of literals
# in stages.js. `git log tools/blender/pc2_entity` still has the code.
#
# Requires the "PicoCad2 Blender Tools" addon for the import step.

import hashlib
import json
import math
import os
import re
import uuid
from contextlib import contextmanager

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Euler, Matrix, Vector
from . import subentities

try:
    from .palettes import PALETTES as PALETTE_LIBRARY
except ImportError:   # loaded as a loose script rather than as the package
    PALETTE_LIBRARY = ()

# --- constants -------------------------------------------------------------

PRIM_COLLECTION = "PC2_Primitives"
MESH_PROP = "pc2_mesh"    # which model.txt mesh this part instances
COLOR_PROP = "pc2_color"  # palette override index; -1 / absent = keep mesh colors
BANK_PROP = "pc2_bank"    # placement only: palette BANK; 0 / absent = the stage's own
UV_PROP = "pc2_uv"        # a retile this addon cannot author, carried verbatim
ORDER_PROP = "pc2_order"  # emit order, so a round-trip is byte-identical
LABEL_PROP = "pc2_label"  # legacy label carrier; the object NAME is the label now
UID_PROP = "pc2_uid"      # stable .blend-only identity; never exported
RIGID_PROP = "pc2_rigid"  # a compound's inner part: follows its parent's SCALE too
STATES_PROP = "pc2_states"  # one JSON document on the entity collection
ENTITY_PROP = "pc2_is_entity"  # marks a collection as one entity's parts
STAGE_PROP = "pc2_is_stage"    # marks a collection as one stage's placements
DEFAULT_TILE_SIZE = 16    # engine.js: uv.tile.size default
TILE_SIZES = ("8", "16", "32", "64")  # what the picker offers

# DERIVED, not hardcoded, so one file works on every machine. Blender loads
# this from its OWN addon dir, but that dir is a junction (Windows) or symlink
# (macOS) into the repo, and realpath follows both -- so the repo is four
# levels up from the resolved file, the same derivation
# tools/blender/tests/_harness.py already uses. KNOWN is the fallback for an
# addon COPIED rather than linked: there is no link to follow, so the guess
# lands inside Blender's own addon dir instead. Both machines are listed, so
# neither of them needs this file edited.
# NOTE src/assets/ is itself a junction to picoCAD2's own asset folder -- it
# holds the model the app saves, and NOTHING generated. Generated modules go in
# src/, or an export lands in %APPDATA%\picocad2\assets and is silently lost.
KNOWN = (r"C:\projects\js13k-2026", "/Users/briannicolucci/js13k-2026")


def repo_root():
    """The repo this addon was installed from; falls back to the KNOWN paths."""
    up = os.path.realpath(__file__)
    for _ in range(4):  # __init__.py -> pc2_entity -> blender -> tools -> repo
        up = os.path.dirname(up)
    for candidate in (up,) + KNOWN:
        if os.path.exists(os.path.join(candidate, "package.json")):
            return candidate
    return up  # nothing matched; the Files subpanel shows where it looked


REPO = repo_root()
DEFAULT_MODEL = os.path.join(REPO, "src", "assets", "model.txt")
DEFAULT_ENTITIES = os.path.join(REPO, "src", "entities.js")
DEFAULT_STAGES = os.path.join(REPO, "src", "stages.js")

# Only written when entities.js is absent entirely; an existing file keeps its
# own header. Kept character-for-character in step with the one the web editor
# generates (src/entities.js), or a from-scratch export here and the next Save
# there would differ for no reason.
DEFAULT_HEADER = (
    "// GENERATED by the Entity editor (/editor_entity.html) — Save regenerates this\n"
    "// file (editor-save middleware in vite.config.ts). A blueprint is an array of\n"
    "// parts for spawnEntity (src/entity.js):\n"
    "//   { mesh, pos, rot (degrees), scale, color?, uv?, parent? }\n"
    "// A trailing `NAME.t = ...` is the TAG: the role the game looks an entity\n"
    "// up by (loadStage in main.js buckets each stage by tag).\n"
    "// `NAME.s` holds Blender-authored pose states; edit their part structure in Blender.\n"
    "// One export per entity: blueprints nothing imports tree-shake out of the\n"
    "// build and cost zero bytes.\n"
)

# Blender (Z-up) -> engine space:
#   importer:  blender = (px, pz, py)   (picoCAD2 is Y-up already; no mirror)
#   pico.js:   engine  = (-px, py, pz)  (X mirror baked at the graph root)
#   therefore  engine  = (-bx, bz, by)
# det(C) = +1, so this is a ROTATION (180 deg about (0,1,1)), not a reflection:
# rigid transforms convert with no handedness fixup. C is also its own inverse,
# so the conjugation below reads C @ M @ C.
C = Matrix(((-1.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0)))


def conjugate(m):
    """Convert a matrix between Blender space and engine space.

    C is its own inverse, so ONE function covers both directions -- there is no
    to_/from_ pair to get backwards. Every basis change goes through here so no
    call site can drop a C and be subtly wrong only for rotated parts.
    """
    return C @ m @ C


# Mirroring is conjugation by the reflection, Q * M * Q -- in ENGINE space, so
# it goes through conjugate() like every other basis-sensitive thing here.
MIRROR_X = Matrix.Diagonal(Vector((-1.0, 1.0, 1.0))).to_4x4()


def mirror_x(m):
    """`m` mirrored across the engine's x = 0 plane of whatever space it is in.

    For a TRS part this comes out as pos.x, rot.y and rot.z negated with the
    scale untouched -- det stays +1, so a mirrored part never needs the
    negative scale that mirroring usually costs (and that the engine would
    then have to draw inside-out). The reflection that is left over lands on
    the MESH instead, and every primitive in model.txt is symmetric about its
    own local YZ plane -- an X flip maps each one's vertex set onto itself --
    so it is invisible.

    Conjugation distributes over products, so mirroring EVERY local matrix in
    a sub-tree mirrors the assembly: Q(AB)Q = (QAQ)(QBQ).

    The outer conjugate() happens to cancel for THIS axis -- C swaps y and z,
    which diag(-1,1,1) does not notice, so conjugate(MIRROR_X) == MIRROR_X and
    the whole expression reduces to MIRROR_X @ m @ MIRROR_X. It is written the
    long way on purpose: that cancellation is a coincidence of the x axis, and
    mirroring across engine y or z would not survive dropping it.

    The one thing that is not symmetric is the UV layout, so a TEXTURED part
    comes out with its texture flipped left/right. A colour-override part
    takes no texture lookup at all and is exact.
    """
    return conjugate(MIRROR_X @ conjugate(m) @ MIRROR_X)

# Only the generated shape is matched, and only ever on a generated file.
# Anchored at the start of a line, or a COMMENTED-OUT block counts: `//` does
# not stop the match, and since its closing `// ];` does not start a line the
# match then runs on to the next real one and swallows whatever block sits
# between them. That is how a commented-out UNICORN made CARNY disappear
# from the entity list.
BLOCK_RE = re.compile(r"^export const (\w+) = \[.*?^\];", re.S | re.M)
STATE_BLOCK_RE = re.compile(r"^(\w+)\.s = \[.*?^\];", re.S | re.M)
SUFFIX_RE = re.compile(r"\.\d{3}$")
COLOR_MAT_RE = re.compile(r"_color_unlit_(\d+)$")
# The placement tint's three nodes, found by NAME so inserting it is
# idempotent -- see placement_tint().
TINT_MIX = "pc2_placement_tint"
TINT_INFO = "pc2_placement_info"
TINT_BASE = "pc2_tint_base"
IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


# --- helpers ---------------------------------------------------------------

def primitives_collection(create=False):
    coll = bpy.data.collections.get(PRIM_COLLECTION)
    if coll is None and create:
        coll = bpy.data.collections.new(PRIM_COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    return coll


def primitive_objects():
    """One object per mesh in the library, in mesh-name order.

    Deduplicated because the buttons ARE model.txt's meshes, and one import can
    already produce two objects for one mesh: two same-named mesh nodes in
    model.txt land as `mesh_x` and `mesh_x.001`, both carrying the same
    MESH_PROP, and without this they would offer two buttons for one primitive.
    Sorting by object name first keeps the real library object ahead of any
    `.001` copy.
    """
    coll = primitives_collection()
    if coll is None:
        return []
    found = {}
    for obj in sorted(coll.objects, key=lambda o: o.name):
        if obj.type == 'MESH' and MESH_PROP in obj:
            found.setdefault(str(obj[MESH_PROP]), obj)
    return [found[name] for name in sorted(found)]


def is_pivot(obj):
    """A part that draws nothing -- Blender's Empty, the blueprint's mesh-less
    part. It exists to be a PARENT: children turn and scale about it rather
    than about their own origins (a jaw hinge, a shoulder).

    A stage PLACEMENT is an Empty too, so it has to be excluded here: dropped
    into an entity collection it would otherwise export as a pivot, silently
    turning a whole placed entity into a hinge.
    """
    return obj.type == 'EMPTY' and (obj.instance_collection is None or subentities.is_link(obj))


PIVOT_NAME = "pivot"


def new_pivot(name):
    """An Empty, drawn as plain axes and small enough not to swamp a part."""
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = 'PLAIN_AXES'
    obj.empty_display_size = 0.25
    return obj


def is_part(obj):
    """Everything the blueprint holds: primitives AND pivots.

    Most of the addon wants this rather than mesh_name_of, which answers a
    narrower question (which mesh does this draw) and says None for a pivot.
    Filtering an entity with mesh_name_of drops pivots on the floor, and a
    child of a dropped parent re-exports as a ROOT carrying a matrix that was
    local to it -- which lands the whole sub-assembly at the entity origin.
    """
    return mesh_name_of(obj) is not None or is_pivot(obj)


def part_uid(obj):
    """Stable authoring identity for state snapshots.

    Runtime states use compact part indices, but indices move when a hierarchy
    is reordered. The UUID stays only in the .blend and is translated to the
    current DFS index on every export, so correctness costs no shipped bytes.
    """
    uid = str(obj.get(UID_PROP) or "")
    if not uid:
        uid = uuid.uuid4().hex
        obj[UID_PROP] = uid
    return uid


def ordered_parts(coll):
    """The runtime order for a collection: DFS, parent before children."""
    objs = [o for o in coll.all_objects if is_part(o)]
    members = set(objs)
    children = {o: [] for o in objs}
    roots = []
    for obj in objs:
        if obj.parent in members:
            children[obj.parent].append(obj)
        else:
            roots.append(obj)
    sort_key = lambda o: (float(o.get(ORDER_PROP, 1e9)), o.name)
    order = []

    def walk(obj):
        order.append(obj)
        for child in sorted(children[obj], key=sort_key):
            walk(child)

    for root in sorted(roots, key=sort_key):
        walk(root)
    # A copied Blender object brings custom properties with it. Keep the first
    # UID and repair later duplicates before a state can key both as one part.
    seen = set()
    for obj in order:
        uid = str(obj.get(UID_PROP) or "")
        if not uid or uid in seen:
            obj[UID_PROP] = uuid.uuid4().hex
        seen.add(str(obj[UID_PROP]))
    return order


def mesh_name_of(obj):
    """Which model.txt mesh a part draws.

    The custom property is authoritative so parts can be renamed freely in the
    outliner ("left_wing"); the datablock name is a fallback for objects made
    by hand with Alt+D.
    """
    name = obj.get(MESH_PROP)
    if name:
        return str(name)
    if obj.type == 'MESH' and obj.data is not None:
        base = SUFFIX_RE.sub("", obj.data.name)
        if base.startswith("mesh_"):
            return base
    return None


def color_of(obj):
    """Palette override index, or None. An explicit property wins; otherwise
    read the importer's per-index solid-colour material (`*_color_unlit_N`)."""
    if COLOR_PROP in obj:
        value = int(obj[COLOR_PROP])
        return None if value < 0 else value & 15
    for slot in obj.material_slots:
        if slot.material is None:
            continue
        match = COLOR_MAT_RE.search(slot.material.name)
        if match:
            return int(match.group(1)) & 15
    return None


def model_mesh_names(path):
    """Mesh node names in model.txt -- the keys main.js builds MESH from."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    names = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("mesh") and node.get("name"):
            names.add(str(node["name"]))
        for child in node.get("children") or []:
            walk(child)

    walk(data.get("graph") or {})
    return names


def find_layer_collection(layer_coll, name):
    if layer_coll.collection.name == name:
        return layer_coll
    for child in layer_coll.children:
        found = find_layer_collection(child, name)
        if found is not None:
            return found
    return None


def activate_collection(context, coll):
    """Make `coll` the active layer collection, so new objects land inside it.

    Returns the PREVIOUSLY active one, for callers that only want it active
    for the length of an operation (Load Primitives restores it).
    """
    previous = context.view_layer.active_layer_collection
    layer = find_layer_collection(context.view_layer.layer_collection, coll.name)
    if layer is not None:
        context.view_layer.active_layer_collection = layer
    return previous


def num(value):
    """Blueprint number: 4dp, trailing zeros trimmed, no -0."""
    value = round(float(value), 4)
    if value == 0:
        value = 0.0
    # "%.4f" always emits a digit before the point, so the two strips can never
    # eat the whole string -- 0.0 comes out "0", not "".
    return ("%.4f" % value).rstrip("0").rstrip(".")


def half_turn(degrees):
    """Pin a half turn to +180.

    R(180) and R(-180) are the SAME matrix on any single axis, so a
    decomposition is free to return either sign and Blender's returns both: an
    X half turn comes back -180, a Y half turn +180. Left alone that breaks the
    byte-identical round-trip for a rotation nothing touched -- a blueprint
    saying `rot: [0, -180, 0]` re-exports as `[0, 180, 0]`. Sign is the only
    thing this changes; the matrix is identical either way, whatever the other
    two axes hold. Applies to ROTATION only: -180 is a perfectly ordinary
    position or scale.
    """
    return 180.0 if round(float(degrees), 4) == -180 else degrees


def emit_trs(target, pos, rot, scale):
    """Write pos/rot/scale into an export dict, omitting whichever is identity.

    One writer, so pos/rot/scale are formatted the same way everywhere and a
    round-trip through Blender cannot rewrite numbers it did not change.
    """
    for key, values, identity in (("pos", pos, 0.0),
                                  ("rot", [half_turn(v) for v in rot], 0.0),
                                  ("scale", scale, 1.0)):
        if any(abs(v - identity) > 1e-4 for v in values):
            target[key] = [num(v) for v in values]


def tile_size(part_props):
    """The tile size as a number. Stored as an enum so the panel can offer the
    four sizes as buttons; every reader wants the int."""
    return int(part_props.uv_tile_size)


def mesh_uv_src(mesh):
    """The mesh's own UV bounds in ENGINE space -- the engine's `u_uvSrc`.

    engine.js computes it as the min/max of the mesh's UVs; the picoCAD2
    importer flipped V on the way in, so flip back before measuring.
    """
    layer = mesh.uv_layers.active if mesh and mesh.uv_layers else None
    if layer is None or not len(layer.data):
        return (0.0, 0.0, 1.0, 1.0)
    us = [d.uv[0] for d in layer.data]
    vs = [1.0 - d.uv[1] for d in layer.data]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    if u1 <= u0 or v1 <= v0:
        return (0.0, 0.0, 1.0, 1.0)
    return (u0, v0, u1 - u0, v1 - v0)


def texture_dims(obj):
    """The atlas size in pixels -- the engine's E.texW / E.texH."""
    _, image = base_texture_image(obj)
    if image is not None and image.size[0] and image.size[1]:
        return image.size[0], image.size[1]
    return 128, 128


def uv_dest_rect(part_props, src, tex_w, tex_h):
    """Where a retile lands, in engine texture fractions -- engine.js's `rect`.

    Mirrors: rect = [(u-1)*size/texW, (v-1)*size/texH, size/texW, size/texH].
    With no tile the destination is the mesh's own rect (shader mode 1, plain
    repeat), which is what `d = v_params.y > 1.5 ? v_uvRect : u_uvSrc` picks.
    """
    if part_props.draws != 'TILE':
        return src
    size = float(tile_size(part_props))
    return ((part_props.uv_tile_u - 1) * size / tex_w,
            (part_props.uv_tile_v - 1) * size / tex_h,
            size / tex_w, size / tex_h)


def retile(uv, src, dst, repeat):
    """The fragment shader's retile(), in Python -- the reference the Blender
    node graph and the exported blueprint are both checked against."""
    local = []
    for i in (0, 1):
        n = (uv[i] - src[i]) / max(src[i + 2], 1e-6) * repeat[i]
        local.append(n - math.floor(n))  # GLSL fract
    return (dst[0] + local[0] * dst[2], dst[1] + local[1] * dst[3])


def loc_rot_only(m):
    """`m` with its scale dropped -- the frame a part hangs off when it follows
    a parent's position and rotation but not its shape."""
    loc, quat, _ = m.decompose()
    return Matrix.LocRotScale(loc, quat, (1.0, 1.0, 1.0))


def scale_free_parent_inverse(parent):
    """Parent inverse that cancels the parent's scale and nothing else.

    Blender computes world = parent.matrix_world * parent_inverse * basis, so
    feeding it P_world^-1 * P_locrot leaves world = P_locrot * basis: the child
    follows the parent around and turns with it, but keeps its own size.

    Baked, not live -- rescaling the parent afterwards leaks its scale back in,
    which is what PC2_OT_drop_inherited_scale re-applies.
    """
    return parent.matrix_world.inverted() @ loc_rot_only(parent.matrix_world)


def hierarchy_depth(obj):
    depth, node = 0, obj
    while node.parent is not None:
        depth += 1
        node = node.parent
    return depth


def part_descendants(obj, members):
    """Every part under `obj`, at any depth, restricted to `members`.

    An assembly is the thing worth operating on -- an eye is a hemisphere and
    the two hemispheres parented to it -- so anything that copies a part takes
    its children too or it copies a shape with its detail missing.
    """
    found, stack = [], [obj]
    while stack:
        for child in stack.pop().children:
            if child in members:
                found.append(child)
                stack.append(child)
    return found


def decompose_matrix(m):
    """Engine-space matrix -> (pos, rot degrees, scale, exact?).

    `exact` is False when the matrix carries shear, which a TRS blueprint part
    has nowhere to store.
    """
    loc, quat, scale = m.decompose()
    # DOMMatrix.rotate(rx, ry, rz) post-multiplies Z, then Y, then X, giving
    # Rz*Ry*Rx -- which is exactly Blender's 'XYZ' euler order.
    euler = quat.to_euler('XYZ')
    rebuilt = Matrix.LocRotScale(loc, quat, scale)
    exact = all(abs(a - b) < 1e-4
                for row_m, row_r in zip(m, rebuilt)
                for a, b in zip(row_m, row_r))
    rot = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
    return list(loc), rot, list(scale), exact


def decompose_part(obj, world=False):
    """A part's transform in engine space, local to its parent by default.

    `matrix_local` is the true local-to-parent transform: it already folds in
    matrix_parent_inverse and delta transforms, so ordinary Ctrl+P parenting
    exports correctly. `matrix_world` is the entity-relative transform, since
    collections do not transform their objects.
    """
    source = obj.matrix_world if world else obj.matrix_local
    return decompose_matrix(conjugate(source))


def serialize_part(part, label):
    """Match the web editor's serializePart output so the two round-trip.

    `label` is written as a trailing comment, which is free: the minifier drops
    it before the bundle is ever compressed.
    """
    fields = ["mesh: '%s'" % part["mesh"]] if "mesh" in part else []
    for key in ("pos", "rot", "scale"):
        if key in part:
            fields.append("%s: [%s]" % (key, ", ".join(part[key])))
    if "color" in part:
        fields.append("color: %d" % part["color"])
    if "uv" in part:
        fields.append("uv: %s" % part["uv"])
    if "parent" in part:
        fields.append("parent: %d" % part["parent"])
    # A pivot sitting at its parent's origin has nothing to say at all.
    line = "  { %s }," % ", ".join(fields) if fields else "  {},"
    return "%s   // %s" % (line, label) if label else line


def uv_to_js(value):
    """Re-emit a uv object the way the web editor's serializePart does, so a
    round-trip through Blender leaves the text untouched."""
    text = json.dumps(value, separators=(",", ":"))
    text = re.sub(r'"([^"]+)":', r"\1: ", text)
    return text.replace(",", ", ").replace("{", "{ ").replace("}", " }")


def parse_blueprint(text, name):
    """One `export const NAME = [...]` block -> a list of part dicts.

    entities.js is generated and its part literals are JSON in all but the
    quoting, so normalising is enough -- no JS engine required.
    """
    block = entity_block(text, name)
    return None if block is None else js_array(block, name)


def js_array(block, name):
    """An `export const NAME = [...]` block -> the array it holds.

    Both generated modules are the same shape -- one literal per line, JSON in
    all but the quoting -- so normalising is enough and no JS engine is needed.
    ONE reader for entities.js and stages.js: a second copy would be a second
    idea of what the generated shape is.
    """
    body = block[block.index("["):].rstrip()
    if body.endswith(";"):
        body = body[:-1]
    body = re.sub(r"//[^\n]*", "", body)                       # line comments
    body = body.replace("'", '"')                              # JS -> JSON quotes
    body = re.sub(r"([{,])\s*([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', body)  # bare keys
    # A stage's `e:` is a bare IDENTIFIER, not a literal -- it is the imported
    # blueprint itself. Nothing in entities.js has an `e` key, so quoting it
    # here costs blueprints nothing.
    body = re.sub(r'"e":\s*([A-Za-z_$][\w$]*)', r'"e": "\1"', body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)                 # trailing commas
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError("%s is not an array" % name)
    return value


def blueprint_trs(part):
    return (
        [float(v) for v in part.get("pos", (0.0, 0.0, 0.0))],
        [float(v) for v in part.get("rot", (0.0, 0.0, 0.0))],
        [float(v) for v in part.get("scale", (1.0, 1.0, 1.0))],
    )


def engine_local(part):
    """The local matrix spawnEntity bakes for a part: T * Rz * Ry * Rx * S.

    LocRotScale builds exactly that product, and is what decompose_matrix uses
    to check its own decomposition -- so the two stay each other's inverse.
    """
    pos, rot, scale = blueprint_trs(part)
    return Matrix.LocRotScale(Vector(pos),
                              Euler([math.radians(v) for v in rot], 'XYZ'),
                              Vector(scale))


def state_local(part, delta):
    """Apply a sparse absolute TRS override without manufacturing shear."""
    pos, rot, scale = blueprint_trs(part)
    if delta:
        if delta[1]:
            pos = [float(v) for v in delta[1]]
        if delta[2]:
            rot = [float(v) for v in delta[2]]
        if delta[3]:
            scale = [float(v) for v in delta[3]]
    return engine_local({"pos": pos, "rot": rot, "scale": scale})


def angle_delta(target, base):
    """Shortest component rotation offset, with an exact half-turn pinned +."""
    delta = (float(target) - float(base) + 180.0) % 360.0 - 180.0
    return 180.0 if abs(delta + 180.0) < 1e-4 else delta


def matrix_values(m):
    return [float(m[row][col]) for row in range(4) for col in range(4)]


def matrix_from_values(values):
    if not isinstance(values, list) or len(values) != 16:
        raise ValueError("state matrix must contain 16 numbers")
    return Matrix(tuple(tuple(float(values[row * 4 + col]) for col in range(4))
                        for row in range(4)))


def empty_state_data():
    return {"v": 1, "base": {}, "states": [], "active": ""}


def state_data(coll):
    raw = coll.get(STATES_PROP)
    if not raw:
        return empty_state_data()
    try:
        data = json.loads(str(raw))
    except Exception as exc:
        raise ValueError("%s has invalid %s JSON: %s" % (coll.name, STATES_PROP, exc))
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("%s has an unsupported %s format" % (coll.name, STATES_PROP))
    data.setdefault("base", {})
    data.setdefault("states", [])
    data.setdefault("active", "")
    return data


def save_state_data(coll, data):
    coll[STATES_PROP] = json.dumps(data, separators=(",", ":"))


def entity_snapshot(coll):
    """Full authoring snapshot, keyed by .blend-only UID.

    Matrices are engine-space LOCAL transforms, not baked world transforms;
    that is the matrix a runtime state offsets before parenting is composed.
    Each part also records the UID of the parent it was captured under, because
    a LOCAL matrix means nothing without one: re-parenting silently reinterprets
    every stored pose, and `rebase_snapshot` needs the old tree to fix it.
    """
    parents = current_parents(ordered_parts(coll))
    return {part_uid(obj): {
                "m": matrix_values(conjugate(local_of(obj))),
                "v": not obj.hide_get(),
                "p": parents[part_uid(obj)],
                **subentities.snapshot_item(obj),
            } for obj in ordered_parts(coll)}


def local_of(obj):
    """What Blender's matrix_local IS -- parent inverse times basis -- read off
    the object rather than the depsgraph. matrix_local is only computed when
    the object is evaluated, and a collection that is not in the scene (an
    entity just imported to be LINKED, say) has never been: it reports the
    identity for every part, and a link built from that draws nothing."""
    return obj.matrix_basis if obj.parent is None else obj.matrix_parent_inverse @ obj.matrix_basis


def current_parents(objects):
    """uid -> parent uid for the entity as it stands now; "" for a root."""
    uids = {obj: part_uid(obj) for obj in objects}
    return {uid: uids.get(obj.parent, "") for obj, uid in uids.items()}


def snapshot_parents(snapshot, objects):
    """uid -> parent uid a snapshot was CAPTURED under.

    Snapshots written before parent links were recorded carry no "p", so they
    can only assume the hierarchy has not moved since. That assumption is why
    Sync States has to run BEFORE a re-parent rather than after: once the old
    tree is gone from the scene, it exists nowhere else.
    """
    live = current_parents(objects)
    return {uid: (item["p"] if "p" in item else live.get(uid, ""))
            for uid, item in snapshot.items()}


def snapshot_drift(snapshot, objects):
    """(re-parented, added, removed) UIDs between a snapshot and the scene."""
    live = current_parents(objects)
    stored = snapshot_parents(snapshot, objects)
    return ([uid for uid in live if uid in snapshot and stored[uid] != live[uid]],
            [uid for uid in live if uid not in snapshot],
            [uid for uid in snapshot if uid not in live])


def states_unstamped(data):
    """True if any snapshot predates parent tracking (see stamp_state_parents).

    Such a snapshot has no record of the tree it was captured in, so every
    question about drift silently answers "none". That is harmless until
    something is re-parented and ruinous immediately after, which is why the
    operators refuse instead of guessing.
    """
    return any("p" not in item
               for snapshot in ([data.get("base") or {}]
                                + [s.get("parts") or {} for s in data.get("states") or []])
               for item in snapshot.values())


def entry_posed(item, base):
    """Whether a state entry MOVES a part: its transform (or a link's pose)
    differs from the base's. Visibility is the state's own either way -- a
    hidden part that was never moved still follows the base's transform."""
    if base is None:
        return True
    if item.get("s", "") != base.get("s", ""):
        return True
    return any(abs(a - b) > 1e-6 for a, b in zip(item["m"], base["m"]))


def mark_posed(parts, base):
    """Stamp each entry with `d`: posed (differs from the base) or not.

    A state stores EVERY part's matrix, but only the parts the author moved are
    the state's business. The rest must follow the base pose wherever it goes
    afterwards -- scale the pupil bigger and the blink must not shrink it back
    -- so which is which is recorded at SAVE time, against the base as it was
    then, and resolve_state substitutes the current base for the unposed ones.
    """
    for uid, item in parts.items():
        item["d"] = entry_posed(item, base.get(uid))
    return parts


def live_base(coll, data):
    """The base pose as it stands: the scene while the base is applied, the
    store otherwise (the scene is then showing some state's pose)."""
    return data["base"] if data.get("active") else entity_snapshot(coll)


def resolve_state(coll, data, saved):
    """A state as a FULL snapshot: its posed parts, the base for the rest.

    Every consumer of a state -- apply, export, a link preview, an expanded
    sub-entity -- goes through here, so none of them can pin a part the author
    never posed. An entry with no `d` (a snapshot written before the flag, or
    one built for an expanded collection) is treated as posed, which is what
    every consumer did before.
    """
    base = live_base(coll, data)
    out = {}
    for uid, item in (saved.get("parts") or {}).items():
        b = base.get(uid)
        if item.get("d", True) or b is None:
            out[uid] = item
        else:
            out[uid] = {**item, "m": b["m"], **({"s": b["s"]} if "s" in b else {})}
    return out


def states_drift(data, objects):
    """Every state's drift, folded into one (re-parented, added, removed)."""
    moved, added, removed = set(), set(), set()
    for saved in data.get("states") or []:
        a, b, c = snapshot_drift(saved.get("parts") or {}, objects)
        moved.update(a)
        added.update(b)
        removed.update(c)
    return sorted(moved), sorted(added), sorted(removed)


def rebase_snapshot(snapshot, objects, fallback):
    """Re-express a snapshot's locals under the CURRENT hierarchy.

    What is preserved is each part's WORLD transform inside the entity -- the
    pose that was actually posed -- while the frame it is written in follows
    the new tree. A part the snapshot predates takes its base local instead, so
    an older state simply does not move a newly added part, and a part that no
    longer exists drops out.

    The composition is done on the STORED engine-space matrices directly rather
    than by round-tripping through Blender, which is safe because `conjugate` is
    a basis change and distributes over products: C(AB)C == (CAC)(CBC).
    """
    live = current_parents(objects)
    stored = snapshot_parents(snapshot, objects)
    world = {}

    def world_of(uid, seen=()):
        if uid in world:
            return world[uid]
        if uid in seen:            # only reachable from hand-edited JSON
            raise ValueError("part hierarchy contains a cycle")
        item = snapshot.get(uid) or fallback.get(uid)
        if item is None:
            raise ValueError("no stored or base matrix for part %s" % uid)
        m = matrix_from_values(item["m"])
        # A part the snapshot knows is composed up the tree it was captured in;
        # one it predates hangs off today's tree in its base pose.
        parent = stored.get(uid, "") if uid in snapshot else live.get(uid, "")
        if parent and (parent in snapshot or parent in fallback):
            m = world_of(parent, seen + (uid,)) @ m
        world[uid] = m
        return m

    rebased = {}
    for uid, parent in live.items():
        item = snapshot.get(uid)
        m = world_of(uid)
        if parent:
            m = world_of(parent).inverted() @ m
        rebased[uid] = {"m": matrix_values(m),
                        "v": bool(item.get("v", True)) if item else True,
                        "p": parent,
                        **({"s": (item or fallback[uid]).get("s", "")}
                           if "s" in (item or fallback[uid]) else {}),
                        # A part the state predates is unposed by definition.
                        **({"d": item["d"]} if item and "d" in item else {} if item else {"d": False})}
    return rebased


def apply_snapshot(context, coll, snapshot):
    order = ordered_parts(coll)
    missing = [obj.name for obj in order if part_uid(obj) not in snapshot]
    if missing:
        raise ValueError("state predates part(s) %s -- run Sync States to Hierarchy"
                         % ", ".join(missing))
    # Parent-first order makes every local matrix resolve against its final
    # parent transform rather than a stale one from the previous arrangement.
    for obj in order:
        item = snapshot[part_uid(obj)]
        obj.matrix_local = conjugate(matrix_from_values(item["m"]))
        obj.hide_set(not bool(item.get("v", True)))
        if subentities.is_link(obj):
            obj[subentities.POSE] = item.get("s", "")
        context.view_layer.update()
    subentities.refresh_all()


def state_block(text, name):
    for match in STATE_BLOCK_RE.finditer(text):
        if match.group(1) == name:
            return match.group(0)
    return None


def parse_states(text, name):
    block = state_block(text, name)
    if block is None:
        return []
    body = block[block.index("["):].rstrip()
    if body.endswith(";"):
        body = body[:-1]
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"0b[01]+", lambda m: str(int(m.group(0)[2:], 2)), body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    states = json.loads(body)
    if not isinstance(states, list):
        raise ValueError("%s.s is not an array" % name)
    return states


def state_labels(text, name, count):
    """Runtime state comments are their free, Blender-only names."""
    block = state_block(text, name) or ""
    labels = []
    for line in block.splitlines()[1:]:
        match = re.search(r"\],\s*//\s*(.*?)\s*$", line)
        if match:
            labels.append(match.group(1))
    return labels + ["STATE_%d" % i for i in range(len(labels), count)]


def runtime_states_to_data(blueprint, states, names, objects, link_poses={}):
    """Expand compact index overrides into robust UID-keyed snapshots.

    `objects` is aligned to the blueprint, None where a part was folded into a
    link; `link_poses` maps a link's index to its source pose in the base and
    in each state, which is what the link entry's `s` records.
    """
    # The blueprint's own `parent` indices are the tree these locals belong to,
    # so an imported snapshot records it the same way a captured one does.
    parts = [(i, part, obj) for i, (part, obj) in enumerate(zip(blueprint, objects)) if obj is not None]
    parents = {}
    for i, part, obj in parts:
        owner = part.get("parent")
        parents[part_uid(obj)] = part_uid(objects[int(owner)]) if owner is not None else ""
    base = {}
    for i, part, obj in parts:
        base[part_uid(obj)] = {"m": matrix_values(engine_local(part)), "v": True,
                               "p": parents[part_uid(obj)],
                               **({"s": link_poses[i][0]} if i in link_poses else {})}
    expanded = []
    for k, (state, name) in enumerate(zip(states, names)):
        mask, deltas = state
        by_index = {int(delta[0]): delta for delta in deltas}
        snapshot = {}
        for i, part, obj in parts:
            local = engine_local(part)
            delta = by_index.get(i)
            if delta:
                local = state_local(part, delta)
            pose = link_poses[i][k + 1] if i in link_poses else None
            snapshot[part_uid(obj)] = {
                "m": matrix_values(local),
                "v": bool(int(mask) >> i & 1),
                "p": parents[part_uid(obj)],
                # The file already says which parts a state poses: the ones it
                # lists an override for. Everything else follows the base, in
                # the .blend exactly as it does in the runtime.
                "d": bool(delta) or (pose is not None and pose != link_poses[i][0]),
                **({"s": pose} if pose is not None else {}),
            }
        expanded.append({"name": name, "parts": snapshot})
    return {"v": 1, "base": base, "states": expanded, "active": ""}


def snapshot_matrices(snapshot, objects, flattened):
    """One matrix per part, in the frame its blueprint part was emitted in.

    A part whose LOCAL carries shear is exported as a ROOT holding its WORLD
    transform, so its states have to be composed up the Blender chain the same
    way: a stored local read against a parent the blueprint no longer has would
    land the part somewhere else entirely. Every other part keeps its local,
    which is what the runtime bakes against its parent.
    """
    world = {}
    frame = {}
    for obj in objects:
        uid = part_uid(obj)
        local = matrix_from_values(snapshot[uid]["m"])
        parent = world.get(obj.parent)
        world[obj] = local if parent is None else parent @ local
        frame[uid] = world[obj] if uid in flattened else local
    return frame


def serialize_runtime_states(coll, name, data, objects, blueprint, flattened=()):
    """UID-keyed full snapshots -> compact index-keyed runtime overrides."""
    if not data["states"]:
        return None
    if len(objects) > 32:
        raise ValueError("visibility masks support at most 32 parts")
    lines = ["%s.s = [" % name]
    for saved in data["states"]:
        snapshot = resolve_state(coll, data, saved)
        missing = [obj.name for obj in objects if part_uid(obj) not in snapshot]
        if missing:
            raise ValueError("state '%s' predates part(s) %s -- run Sync States to Hierarchy"
                             % (saved.get("name", ""), ", ".join(missing)))
        matrices = snapshot_matrices(snapshot, objects, flattened)
        mask = 0
        deltas = []
        for i, (obj, part) in enumerate(zip(objects, blueprint)):
            if any(float(v) < -1e-4 for v in part.get("scale", (1, 1, 1))):
                raise ValueError("base part '%s' has negative scale" % obj.name)
            item = snapshot[part_uid(obj)]
            if item.get("v", True):
                mask |= 1 << i
            target = matrices[part_uid(obj)]
            pos, rot, scale, target_exact = decompose_matrix(target)
            if not target_exact:
                raise ValueError("state '%s', part '%s' contains shear"
                                 % (saved.get("name", ""), obj.name))
            if any(v < -1e-4 for v in scale):
                raise ValueError("state '%s', part '%s' has negative scale"
                                 % (saved.get("name", ""), obj.name))
            base_pos, base_rot, base_scale = blueprint_trs(part)
            # A zero matrix contains no orientation to decompose. Rotation is
            # invisible at [0,0,0] scale, so inherit the base rather than emit
            # a meaningless override caused only by that lost information.
            if all(abs(v) < 1e-4 for v in scale):
                rot = base_rot
            delta = {}
            if any(abs(a - b) > 1e-4 for a, b in zip(pos, base_pos)):
                delta["pos"] = [num(v) for v in pos]
            rot = [b + angle_delta(a, b) for a, b in zip(rot, base_rot)]
            if any(abs(a - b) > 1e-4 for a, b in zip(rot, base_rot)):
                delta["rot"] = [num(half_turn(v)) for v in rot]
            if any(abs(a - b) > 1e-4 for a, b in zip(scale, base_scale)):
                delta["scale"] = [num(v) for v in scale]
            if delta:
                fields = [str(i)]
                for key in ("pos", "rot", "scale"):
                    fields.append("[%s]" % ", ".join(delta[key]) if key in delta else "0")
                deltas.append("[%s]" % ", ".join(fields))
        bits = "0b" + format(mask, "0%db" % len(objects))
        label = re.sub(r"[\r\n]+", " ", str(saved.get("name") or "STATE")).strip()
        lines.append("  [%s, [%s]],   // %s" % (bits, ", ".join(deltas), label))
    lines.append("];")
    return "\n".join(lines)


def upsert_entity(text, name, block):
    """Replace one `export const NAME = [...]` block, leaving every other
    entity in the file untouched -- main.js imports CAPSULE/ROCKET, so a
    whole-file rewrite from Blender would break the game."""
    for match in BLOCK_RE.finditer(text):
        if match.group(1) == name:
            return text[:match.start()] + block + text[match.end():]
    return text.rstrip("\n") + "\n\n" + block + "\n"


def upsert_states(text, name, block):
    previous = state_block(text, name)
    if previous is not None:
        start = text.index(previous)
        end = start + len(previous)
        if block:
            return text[:start] + block + text[end:]
        # Removing the last state removes its generated property too.
        if end < len(text) and text[end] == "\n":
            end += 1
        return text[:start] + text[end:]
    if not block:
        return text
    entity = entity_block(text, name)
    if entity is None:
        raise ValueError("cannot attach states: %s blueprint is missing" % name)
    end = text.index(entity) + len(entity)
    return text[:end] + "\n" + block + text[end:]


def existing_entity_names(text):
    return set(match.group(1) for match in BLOCK_RE.finditer(text))


# A part's label is a trailing line comment, NOT a field. That is the whole
# point: esbuild strips comments long before Roadroller sees the bundle, so a
# pivot can say what it is for ZERO shipped bytes, where an `n: 'head_rot'`
# field measured ~5 bytes each. Generated blueprints are one part per line, so
# the line IS the part.
PART_LINE_RE = re.compile(r"^\s*\{")
# A linked sub-entity's pivot is labelled `@SOURCE`, or `name @SOURCE` when the
# link was renamed. The file is flat -- the link's parts follow, expanded -- so
# the marker is what lets an import rebuild the link instead of the parts.
LINK_LABEL_RE = re.compile(r"^(?:(.+?)\s+)?@(\w+)$")
PART_LABEL_RE = re.compile(r"^\s*\{.*\},\s*//\s*(\S.*?)\s*$")


def blueprint_labels(text, name):
    """Part index -> its trailing `// label`, for the parts that carry one."""
    block = entity_block(text, name)
    labels = {}
    if block is None:
        return labels
    index = -1
    for line in block.splitlines():
        if not PART_LINE_RE.match(line):
            continue
        index += 1
        match = PART_LABEL_RE.match(line)
        if match:
            labels[index] = match.group(1)
    return labels


def label_of(obj):
    """What to write after a part's line.

    It is the object's own NAME -- what you rename in Blender's outliner --
    for a MESH part exactly as for a pivot, minus the .001 Blender appends
    when a name is already taken. Two entities with a `head_rot` each, or two
    parts drawing the same primitive, would otherwise stop round-tripping
    byte-identically.

    A name that says nothing is left off, so a blueprint nobody has labelled
    stays textually unchanged: `pivot` for an Empty, and for a mesh part the
    name of the mesh it draws, which is what an unrenamed part is called.

    Renaming cannot lose which primitive a part draws -- mesh_name_of reads
    the pc2_mesh property, not the object name.

    LABEL_PROP is only a fallback now. Before this addon could author a mesh
    label it carried one verbatim (from the web editor or a hand edit), the
    way pc2_uv carries a rect; a rename wins over it.
    """
    name = SUFFIX_RE.sub("", obj.name).strip()
    default = PIVOT_NAME if is_pivot(obj) else (mesh_name_of(obj) or "")
    if name and name != default:
        return name
    carried = obj.get(LABEL_PROP)
    return str(carried) if carried else None


def entity_block(text, name):
    """The `export const NAME = [...]` an export is about to replace."""
    for match in BLOCK_RE.finditer(text):
        if match.group(1) == name:
            return match.group(0)
    return None


# --- the entity library ----------------------------------------------------
#
# What you can edit is the union of the blueprints in entities.js and the
# collections in this .blend, so an entity made here shows up in the panel
# before it has ever been exported.

_file_names = {}


def file_block_names(path):
    """The `export const NAME` list in a generated module, re-read only when
    that file changes.

    The panel asks for this on every redraw -- and the web Entity editor
    writes entities.js behind our back -- so it has to be live, but not
    re-parsed a hundred times a second. Keyed by PATH rather than holding one
    slot, because the panel now asks about entities.js and stages.js in the
    same draw, and a single slot would make each question evict the other's
    answer and re-read both files every frame.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ()
    cached = _file_names.get(path)
    if cached is None or cached[0] != mtime:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                names = tuple(sorted(existing_entity_names(handle.read())))
        except Exception:
            names = ()
        cached = _file_names[path] = (mtime, names)
    return cached[1]


def entity_collections():
    """Collections in this .blend that hold an entity's parts.

    The marker is what makes an EMPTY one count: listing only collections that
    already contain a mesh is why a brand new entity used to be invisible in
    the panel until its first part was added.
    """
    found = []
    for coll in bpy.data.collections:
        if subentities.PREVIEW in coll:
            continue
        if coll.name == PRIM_COLLECTION or not IDENT_RE.match(coll.name):
            continue
        if coll.get(ENTITY_PROP) or any(mesh_name_of(o) for o in coll.all_objects):
            found.append(coll)
    return found


def entity_names(context):
    """Every entity the panel lists: exported and not-yet-exported alike."""
    path = bpy.path.abspath(context.scene.pc2_entity.entities_path)
    return sorted(set(file_block_names(path)) | {c.name for c in entity_collections()})


def show_only(context, coll):
    """Link `coll` into the scene and unlink everything else this addon owns.

    Blueprints are authored around the origin, so entities left linked together
    sit inside each other -- and a stage's entities must be out of the scene as
    well, or every placement is shadowed by one extra copy of its source
    sitting at the world origin. So one thing is on screen at a time, entity or
    stage, and this is the only place that decides which.

    Unlinking is safe because every collection it owns carries a fake user: it
    survives in the .blend with nothing in the scene referencing it, and these
    panels are what browse them. Only ever unlink collections this addon
    MARKED: entity_collections() also matches anything holding a picoCAD2 mesh,
    and hiding a user's own collection because it happened to qualify would be
    a nasty trick.
    """
    scene = context.scene.collection
    for other in bpy.data.collections:
        if other is coll or not (other.get(ENTITY_PROP) or other.get(STAGE_PROP)):
            continue
        if other.name in scene.children:
            scene.children.unlink(other)
    if coll.name not in scene.children:
        scene.children.link(coll)
    activate_collection(context, coll)


def focus_entity(context, coll):
    """Show `coll` on its own and make it the entity parts are added to."""
    coll[ENTITY_PROP] = True
    coll.use_fake_user = True
    show_only(context, coll)
    context.scene.pc2_entity.entity = coll
    # Parts just linked in have no evaluated matrix_world yet, which the export's
    # shear fallback and Drop Inherited Scale both read.
    context.view_layer.update()


def build_entity_collection(context, name):
    """Read one blueprint out of entities.js into a new collection.

    Raises ValueError carrying a message meant for the user. Linking it into
    the scene is focus_entity's job, not this one's.
    """
    props = context.scene.pc2_entity
    prims = {str(o[MESH_PROP]): o for o in primitive_objects()}
    if not prims:
        raise ValueError("No primitives loaded -- check the model path under Files")
    if bpy.data.collections.get(name) is not None:
        raise ValueError("A collection named '%s' already exists -- rename or delete it, "
                         "otherwise Blender would call this one '%s.001' and export it "
                         "under the wrong name" % (name, name))

    path = bpy.path.abspath(props.entities_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        blueprint = parse_blueprint(source, name)
    except Exception as exc:
        raise ValueError("Could not parse '%s': %s" % (name, exc))
    if blueprint is None:
        raise ValueError("'%s' is not in %s" % (name, os.path.basename(path)))
    missing = sorted(set(p.get("mesh") for p in blueprint) - set(prims) - {None})
    if missing:
        raise ValueError("Not in the primitive library: %s" % ", ".join(missing))

    labels = blueprint_labels(source, name)
    states = parse_states(source, name) or []
    names = state_labels(source, name, len(states))
    links = resolve_file_links(context, source, name, blueprint, labels, states)
    skipped = {k for i in links for k in blueprint_descendants(blueprint, i)}

    coll = bpy.data.collections.new(name)
    coll[ENTITY_PROP] = True
    created = []
    for i, part in enumerate(blueprint):
        mesh = part.get("mesh")
        if i in skipped:
            # Expanded copy of a linked sub-entity: the link below draws it.
            created.append(None)
            continue
        if i in links:
            src, link_name, poses, mirrored, note = links[i]
            obj = new_pivot(link_name)
            obj[subentities.MARK] = True
            obj.pc2_source = bpy.data.collections[src]
            obj[subentities.POSE] = poses[0]
            if mirrored:
                obj[subentities.MIRROR] = True
            if note:
                obj["pc2_link_note"] = note
                print("[pc2] %s: '%s' -- %s" % (name, link_name, note))
        elif mesh is None:
            # A pivot. Blender's own name for this is an Empty, and its
            # transform is the whole of it -- no mesh, no colour, no uv. Its
            # label becomes the object name, which is where you rename it.
            obj = new_pivot(labels.get(i) or PIVOT_NAME)
        else:
            # The label becomes the object NAME, the same as a pivot's, so
            # renaming in the outliner is how a part is labelled and the
            # outliner reads like the blueprint. pc2_mesh still says which
            # primitive it draws, so the name is free to be anything.
            obj = bpy.data.objects.new(labels.get(i) or mesh, prims[mesh].data)
            obj[MESH_PROP] = mesh
        part_uid(obj)
        obj[ORDER_PROP] = i
        coll.objects.link(obj)
        # uv first, then colour: a part carrying both is drawn flat by the
        # engine, so colour is the mode that must win. The tile stays in the
        # fields, remembered.
        if "uv" in part:
            apply_uv_spec(obj, part["uv"])
        if "color" in part:
            obj[COLOR_PROP] = int(part["color"])
            obj.pc2.draws = 'COLOR'
        refresh_part_preview(obj)
        created.append(obj)

    for part, obj in zip(blueprint, created):
        if obj is None:
            continue
        if "parent" in part:
            # spawnEntity composes parent.local * local, which is exactly
            # Blender parenting with an identity parent inverse.
            obj.parent = created[int(part["parent"])]
            obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = conjugate(engine_local(part))
    if states or links:
        save_state_data(coll, runtime_states_to_data(blueprint, states, names, created,
                                                     {i: links[i][2] for i in links}))
    for i in links:
        subentities.refresh(created[i])
    return coll, len([o for o in created if o is not None])


def blueprint_descendants(blueprint, root):
    """Indices of every part under `root` -- a link's expanded copy."""
    out = set()
    for i, part in enumerate(blueprint):
        j = part.get("parent")
        while j is not None:
            if int(j) == root:
                out.add(i)
                break
            j = blueprint[int(j)].get("parent")
    return out


def pose_of_expansion(blueprint, inner, pivot, mask, deltas, source):
    """Which pose of `source` a link's expanded parts are in, in one state.

    The file holds the expansion as plain parts, so the link's pose has to be
    READ BACK by comparing the parts against the source's own expansion in its
    base and in each of its poses -- the source as it lives in this .blend,
    not its block in the file, since a stale block is exactly what a link is
    there to fix. Two passes: STRICT compares every part, which is exact when
    the file is fresh and also tells a mirrored link from a plain one; when
    nothing matches strictly the block is stale, and the LENIENT pass compares
    only what a pose changes -- the parts it moves, and which it hides --
    since the unposed parts follow the base anyway and are precisely the ones
    that went stale. Returns (pose, mirrored), or None.
    """
    by = {int(d[0]): d for d in deltas}
    got = [state_local(blueprint[k], by[k]) if k in by else engine_local(blueprint[k]) for k in inner]
    posed = {n for n, k in enumerate(inner) if k in by}
    visible = mask is None or bool(int(mask) >> pivot & 1)
    vis = [mask is None or bool(int(mask) >> k & 1) for k in inner]

    def same(a, b):
        return all(abs(x - y) < 2e-3 for x, y in zip(matrix_values(a), matrix_values(b)))

    def rows_of(pose, mirrored):
        try:
            rows = subentities.expand(source, subentities.snapshot(source, pose), mirrored=mirrored)
        except (ValueError, RuntimeError):
            return None
        return rows if len(rows) == len(got) else None

    poses = [""] + [st["name"] for st in state_data(source)["states"]]
    for strict in (True, False):
        for mirrored in (False, True):
            base_rows = rows_of("", mirrored)
            if base_rows is None:
                continue
            for pose in poses:
                rows = rows_of(pose, mirrored)
                if rows is None:
                    continue
                if visible and vis != [row[4] for row in rows]:
                    continue
                if strict:
                    if all(same(g, row[3]) for g, row in zip(got, rows)):
                        return pose, mirrored
                    continue
                moved = {n for n, (row, base) in enumerate(zip(rows, base_rows)) if not same(row[3], base[3])}
                if moved == posed and all(same(got[n], rows[n][3]) for n in posed):
                    return pose, mirrored
    return None


_importing = set()


def resolve_file_links(context, text, name, blueprint, labels, states):
    """index -> (source, link name, [pose per base+state], mirrored, note) for
    every `@SOURCE` pivot, importing the source first if this .blend has not
    got it. The marker is authored intent, so it always yields a link; a
    state whose numbers match none of the source's poses gets the base pose
    and a note the Part panel shows, rather than five dead parts."""
    links = {}
    inside = set()   # parts folded into a link already found: a nested marker is theirs
    for i, part in enumerate(blueprint):
        match = LINK_LABEL_RE.match(labels.get(i) or "")
        if part.get("mesh") is not None or not match or i in inside:
            continue
        link_name, src = match.group(1) or match.group(2), match.group(2)
        if src == name or src in _importing:
            continue
        source = bpy.data.collections.get(src)
        if source is None:
            _importing.add(name)
            try:
                source, _ = build_entity_collection(context, src)
            except ValueError as exc:
                print("[pc2] %s: '%s' links %s, which will not import: %s" % (name, link_name, src, exc))
                continue
            finally:
                _importing.discard(name)
            source.use_fake_user = True
        inner = sorted(blueprint_descendants(blueprint, i))
        poses, mirrored, notes = [], None, []
        for k, (mask, deltas) in enumerate([(None, [])] + list(states)):
            found = pose_of_expansion(blueprint, inner, i, mask, deltas, source)
            if found is None or (mirrored is not None and found[1] != mirrored):
                poses.append("")
                notes.append("base" if k == 0 else "state %d" % (k - 1))
                continue
            poses.append(found[0])
            mirrored = found[1]
        note = ("%s: no pose of %s matched -- base pose used" % (", ".join(notes), src)) if notes else ""
        links[i] = (src, link_name, poses, bool(mirrored), note)
        inside.update(inner)
    return links


# --- stages: a level as placed entity instances -----------------------------
#
# A stage is a collection of collection-INSTANCES: one Empty per placement,
# pointing at the entity's own collection. Blender's linked duplicate is the
# engine's instance one level up, exactly as it is for a part -- so a stage
# needs no authoring concept the entity side has not already got, and an entity
# edited here moves in every stage that places it.
#
# Recovering a placement is reading that Empty's matrix. The depsgraph instance
# walk this used to do (which also caught Geometry Nodes scattering) is in
# `git log tools/blender/pc2_entity`: it was most of the addon for a job that is
# a handful of literals.

# Only written when stages.js is absent entirely; an existing file keeps its own
# header verbatim. A header is PROSE, and prose is the one thing a generator
# should not overwrite.
DEFAULT_STAGE_HEADER = (
    "// GENERATED by the Blender addon's Stage panel (tools/blender/pc2_entity).\n"
    "// A stage is one level: a flat list of placed entities, loaded on its own.\n"
    "//   e = the blueprint itself (imported, so unused entities still tree-shake)\n"
    "//   p / r / s = pos / rot (degrees, DOMMatrix.rotate order) / scale, each\n"
    "//   omitted when it is the identity.\n"
    "//   c = a palette colour override for the whole entity.\n"
    "//   b = the palette BANK it resolves its indices through, 0 (the stage's\n"
    "//   own colours) by default. A higher bank is a whole second 16-colour\n"
    "//   scheme, so two placements of the SAME blueprint can wear different\n"
    "//   colours in one frame; main.js's PALETTE_BANKS says what each holds.\n"
    "// One export per stage, for the same reason entities.js has one per entity:\n"
    "// a stage nothing imports costs zero packed bytes.\n"
    "// main.js's loadStage is what reads all this -- it bakes p/r/s with\n"
    "// entity.js's `trs`, the same composition a blueprint part gets.\n"
)

USES_RE = re.compile(r"\be: (\w+)\b")
STAGE_IMPORT_RE = re.compile(r"^import \{[^}]*\} from '\./entities\.js';$", re.M)


def is_placement(obj):
    """One placed entity: an Empty instancing an entity's collection."""
    return (obj is not None and obj.type == 'EMPTY'
            and obj.instance_type == 'COLLECTION'
            and obj.instance_collection is not None)


def is_stage_guide(obj):
    """PLACEMENT helpers (including Blender's .001 copies) never ship."""
    return obj.name.upper().startswith('PLACEMENT') or obj.get('pc2_stage_guide', False)


def sign_guide(context, coll):
    """A rough HUD sign envelope, projected onto the camera's target plane.

    Just an outline, not lettering. Rebuild explicitly after changing aspect;
    ordinary stage switches preserve any manual adjustment to the guide.
    """
    obj = next((o for o in coll.objects if o.get('pc2_sign_guide')), None)
    if obj is None:
        mesh = bpy.data.meshes.new('PLACEMENT sign outline')
        obj = bpy.data.objects.new('PLACEMENT Sign', mesh)
        coll.objects.link(obj)
        obj['pc2_stage_guide'] = True
        obj['pc2_sign_guide'] = True
    props = context.scene.pc2_entity
    at, dist, _ = stage_view(context)
    height = 2 * dist * math.tan(math.radians(props.cam_fov) / 2)
    width = height * props.cam_res_x / props.cam_res_y
    # Approximate combined QUEENIE'S arc / PLAYLAND envelope. The runtime
    # rounds glyph sizes to HUD pixels, so this is deliberately a guide.
    left, right = -width * .39, width * .39
    top, bottom = height / 2 - width * .015, height / 2 - width * .3
    obj.data.clear_geometry()
    obj.data.from_pydata([(left, top, -dist), (right, top, -dist),
                          (right, bottom, -dist), (left, bottom, -dist)],
                         [(0, 1), (1, 2), (2, 3), (3, 0)], [])
    obj.matrix_world = camera_world(props.cam_yaw, props.cam_pitch, dist, at)
    obj.show_in_front = True
    obj.show_name = True
    obj.hide_render = True
    return obj


def stage_collections():
    return [c for c in bpy.data.collections
            if c.get(STAGE_PROP) and IDENT_RE.match(c.name)]


def stage_names(context):
    """Every stage the panel lists: exported and not-yet-exported alike."""
    path = bpy.path.abspath(context.scene.pc2_entity.stages_path)
    return sorted(set(file_block_names(path)) | {c.name for c in stage_collections()})


def placements_of(coll):
    """A stage's placements in export order.

    ORDER_PROP is the same trick the parts use: an authored stage comes back
    out in the order it went in, so moving one instance cannot reshuffle the
    file. A placement made here has no order yet and sorts to the end.
    """
    if coll is None:
        return []
    objs = [o for o in coll.objects if is_placement(o) and not is_stage_guide(o)]
    objs.sort(key=lambda o: (float(o.get(ORDER_PROP, 1e9)), o.name))
    return objs


def placement_matrix(obj):
    """A placement's transform in BLENDER space -- the engine's placement matrix.

    Blender draws a collection instance offset by the collection's own
    instance_offset, so that has to come back out or a stage authored against a
    shifted collection would export somewhere else. It is zero for every entity
    this addon builds; folding it in on both sides is what keeps import and
    export exact inverses whatever it holds.
    """
    return obj.matrix_world @ Matrix.Translation(-obj.instance_collection.instance_offset)


def placement_local(entry):
    """A stages.js entry -> the engine-space matrix main.js's `trs` bakes for it.

    Same composition as a blueprint part's, which is not a coincidence:
    loadStage hands p/r/s to the very same `trs`.
    """
    return engine_local({"pos": entry.get("p", (0.0, 0.0, 0.0)),
                         "rot": entry.get("r", (0.0, 0.0, 0.0)),
                         "scale": entry.get("s", (1.0, 1.0, 1.0))})


def parse_stage(text, name):
    """One `export const NAME = [...]` block of stages.js -> placement dicts."""
    block = entity_block(text, name)
    return None if block is None else js_array(block, name)


def new_placement(source, color, order, matrix, bank=None):
    """The inverse of placement_matrix: put a placement where the entry says."""
    obj = bpy.data.objects.new(source.name, None)
    obj.instance_type = 'COLLECTION'
    obj.instance_collection = source
    obj.empty_display_size = 0.4
    obj[ORDER_PROP] = order
    if color is not None:
        obj[COLOR_PROP] = int(color)
    # Falsy covers both absent and 0, and 0 IS the default, so a stage with no
    # banks carries no bank property and re-exports exactly as it arrived.
    if bank:
        obj[BANK_PROP] = int(bank)
    obj.matrix_basis = matrix @ Matrix.Translation(source.instance_offset)
    sync_instance_color(obj)
    return obj


def build_stage_collection(context, name):
    """Read one stage out of stages.js into a collection of placements.

    Raises ValueError carrying a message meant for the user. Entities the stage
    places are imported on demand, so opening a level in a fresh .blend brings
    its cast with it.
    """
    props = context.scene.pc2_entity
    if bpy.data.collections.get(name) is not None:
        raise ValueError("A collection named '%s' already exists -- rename or delete it, "
                         "otherwise Blender would call this one '%s.001' and export it "
                         "under the wrong name" % (name, name))
    path = bpy.path.abspath(props.stages_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            entries = parse_stage(handle.read(), name)
    except Exception as exc:
        raise ValueError("Could not parse '%s': %s" % (name, exc))
    if entries is None:
        raise ValueError("'%s' is not in %s" % (name, os.path.basename(path)))

    coll = bpy.data.collections.new(name)
    coll[STAGE_PROP] = True
    coll.use_fake_user = True
    for i, entry in enumerate(entries):
        entity = entry.get("e")
        if not entity:
            raise ValueError("placement %d has no entity" % i)
        source = bpy.data.collections.get(entity)
        if source is None:
            try:
                source, _ = build_entity_collection(context, entity)
            except ValueError as exc:
                raise ValueError("%s places %s, which will not import: %s"
                                 % (name, entity, exc))
            # Nothing in the scene references an entity collection while a
            # stage is up -- only the instances do -- so without a fake user
            # Blender purges it on the next save.
            source.use_fake_user = True
        coll.objects.link(new_placement(source, entry.get("c"), i,
                                        conjugate(placement_local(entry)),
                                        entry.get("b")))
    return coll, len(entries)


def focus_stage(context, coll):
    """Show `coll` on its own and make it the stage placements go into."""
    coll[STAGE_PROP] = True
    coll.use_fake_user = True
    show_only(context, coll)
    context.scene.pc2_entity.stage = coll
    if coll.name == 'INTRO' and not any(o.get('pc2_sign_guide') for o in coll.objects):
        sign_guide(context, coll)
    # Instances just linked in have no evaluated matrix_world yet, which the
    # export reads.
    context.view_layer.update()
    # Only if it is already there: the camera appears when it is asked for, and
    # then follows every stage switch rather than needing a second click.
    if bpy.data.objects.get(CAMERA_NAME):
        place_camera(context)
    # A stage's mood is a different 16 colours, so if the swatches are showing
    # one, switching stage has to repaint them.
    if context.scene.pc2_entity.palette == PALETTE_STAGE:
        apply_palette(context)


# --- the game camera, shown in Blender --------------------------------------
#
# One fixed camera for every stage, exported to game_view.js. Nothing is ever
# fitted to a stage: the frame is the constant and a LEVEL is composed to fit
# it, so a bigger prop in one level moves the prop, never the camera.
#
# The frame is Blender's own: a real camera with the engine's vertical FOV and
# clipping, marked as the scene camera, so the passepartout in camera view is
# the game's viewport and the frustum drawn in the outliner is the game's
# frustum. Only its numeric settings ship, not the Blender camera object.

CAMERA_NAME = "PC2_Camera"
def conjugate_point(v):
    """A POINT through the same basis change `conjugate` applies to a matrix."""
    return C.to_3x3() @ Vector(v[:3])


def stage_view(context):
    """(at, dist, radius): the one authored camera, whatever stage is up.

    `radius` is the runtime's constant far-plane budget (main.js's `radius`
    is 10 and only ever feeds `dist + radius * 3`), not a measurement.
    """
    props = context.scene.pc2_entity
    return list(props.cam_at), props.cam_dist, 10.0


def stage_fit(context, coll):
    """Which placements are fully in shot: (inside, total, [names outside]).

    The question a FIXED camera makes worth asking -- "does this level fit" --
    and it has to be answered against the AABB, not the origin: an object whose
    centre is in frame can still hang out of it. Tested in VIEW space against
    the frustum planes rather than by projecting: a point behind the eye has a
    negative w and would come back inside the frame after the divide.
    """
    at, dist, radius = stage_view(context)
    props = context.scene.pc2_entity
    view = (C @ camera_world(props.cam_yaw, props.cam_pitch, dist, at)).inverted()
    tan = math.tan(math.radians(props.cam_fov) / 2)
    aspect = props.cam_res_x / max(1, props.cam_res_y)
    far = max(1.0, dist + radius * 3)

    inside, outside = 0, []
    for obj in placements_of(coll):
        place = placement_matrix(obj)
        corners = [conjugate_point(place @ part.matrix_world @ Vector(corner))
                   for part in obj.instance_collection.all_objects
                   if part.type == 'MESH' for corner in part.bound_box]
        if not corners:
            continue
        ok = True
        for point in corners:
            x, y, z = view @ Vector(point)
            depth = -z
            if not (0.1 <= depth <= far and abs(x) <= depth * tan * aspect
                    and abs(y) <= depth * tan):
                ok = False
                break
        if ok:
            inside += 1
        else:
            outside.append(obj.instance_collection.name)
    return inside, inside + len(outside), outside


def camera_world(yaw, pitch, dist, at):
    """Where the eye is, in Blender space.

    engine.js's orbitView is T(0,0,-dist) * Rx(pitch) * Ry(yaw) * T(-at) --
    DOMMatrix.rotate(a, b, c) post-multiplies Rz*Ry*Rx, so the two single-axis
    calls read straight off. A camera's transform is that view INVERTED.

    The basis change here is ONE-SIDED (`C @ m`), which is the one place in this
    addon that `conjugate`'s two-sided `C @ m @ C` is the wrong tool. A part's
    local space is the MESH's, which is a picoCAD space and has to be converted
    like the world it sits in -- hence C on both ends. A camera's local space is
    EYE space, and Blender's camera and OpenGL agree on it exactly: -Z forward,
    +Y up. There is nothing to convert on that side, so a second C would rotate
    the camera's own axes for no reason. It is not a subtle error either: with C
    on both ends, C @ (0,0,-1) is engine -Y, so at pitch 0 the camera looked
    straight DOWN however it was aimed.
    """
    view = (Matrix.Translation((0.0, 0.0, -dist))
            @ Matrix.Rotation(math.radians(pitch), 4, 'X')
            @ Matrix.Rotation(math.radians(yaw), 4, 'Y')
            @ Matrix.Translation((-at[0], -at[1], -at[2])))
    return C @ view.inverted()


def place_camera(context):
    """Create or re-aim the one camera, for the active stage. Returns its info."""
    props = context.scene.pc2_entity
    if props.stage is None:
        return None
    at, dist, radius = stage_view(context)

    obj = bpy.data.objects.get(CAMERA_NAME)
    if obj is None or obj.type != 'CAMERA':
        obj = bpy.data.objects.new(CAMERA_NAME, bpy.data.cameras.new(CAMERA_NAME))
    if obj.name not in context.scene.collection.objects:
        # The scene root, never the stage collection: it is not a placement, it
        # must survive a stage switch, and it must not follow one out of view.
        context.scene.collection.objects.link(obj)

    cam = obj.data
    # perspective(35, aspect, far) puts `f` in the Y row and `f / aspect` in X,
    # so the exported field of view is VERTICAL.
    cam.sensor_fit = 'VERTICAL'
    cam.angle_y = math.radians(props.cam_fov)
    cam.clip_start = 0.1                       # engine.js: the near plane
    cam.clip_end = max(1.0, dist + radius * 3) # main.js: far follows the zoom
    cam.show_passepartout = True
    cam.passepartout_alpha = 0.9
    cam.display_size = 0.5
    obj.matrix_world = camera_world(props.cam_yaw, props.cam_pitch, dist, at)

    scene = context.scene
    scene.camera = obj
    # The exported design dimensions set the game's aspect, with black margins
    # outside the frame. Use square pixels in Blender just as in the browser.
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.resolution_x = props.cam_res_x
    scene.render.resolution_y = props.cam_res_y
    scene.render.resolution_percentage = 100
    return {"at": at, "dist": dist, "radius": radius}


GAME_VIEW_KEYS = ("width", "height", "at", "yaw", "pitch", "dist", "fov")
GAME_VIEW_RE = re.compile(r"(\w+):\s*(\[[^\]]*\]|-?\d+(?:\.\d+)?)")
_game_view_cache = {}


def game_view_path(props):
    """game_view.js sits beside stages.js; a path that cannot drift cannot be half-set."""
    return os.path.join(os.path.dirname(bpy.path.abspath(props.stages_path)), "game_view.js")


def read_game_view(path):
    """The game's ONE camera, as `GAME_VIEW` in game_view.js -- or None.

    Cached by mtime because the Stage panel asks on every redraw. The literal
    is what Export Stage writes, so a key-by-key scan of `{...}` is enough.
    """
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    hit = _game_view_cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    start = text.find("GAME_VIEW")
    body = text[text.find("{", start):text.find("}", start)] if start >= 0 else ""
    values = {}
    for key, raw in GAME_VIEW_RE.findall(body):
        values[key] = ([float(v) for v in raw.strip("[]").split(",")]
                       if raw.startswith("[") else float(raw))
    view = values if all(k in values for k in GAME_VIEW_KEYS) else None
    _game_view_cache[path] = (stamp, view)
    return view


def apply_game_view(context, view):
    """Fields <- game_view.js, then ONE re-aim (a per-field re-aim would jump)."""
    props = context.scene.pc2_entity
    _camera_sync["busy"] = True
    try:
        props.cam_res_x, props.cam_res_y = int(view["width"]), int(view["height"])
        props.cam_at = view["at"]
        props.cam_yaw, props.cam_pitch = view["yaw"], view["pitch"]
        props.cam_dist, props.cam_fov = view["dist"], view["fov"]
    finally:
        _camera_sync["busy"] = False
    if bpy.data.objects.get(CAMERA_NAME):
        place_camera(context)


def camera_matches_game_view(props, view):
    """Whether the fields ARE the game's camera, to export precision."""
    return (props.cam_res_x == int(view["width"]) and props.cam_res_y == int(view["height"])
            and all(num(a) == num(b) for a, b in zip(props.cam_at, view["at"]))
            and all(num(getattr(props, "cam_" + k)) == num(view[k])
                    for k in ("yaw", "pitch", "dist", "fov")))


def wrap_degrees(a):
    """Into -180..180, so 180 and -180 compare equal rather than 360 apart."""
    return (a + 180.0) % 360.0 - 180.0


def camera_fields(obj, dist):
    """(yaw, pitch, at, roll) read OFF the Blender camera, in engine convention.

    The inverse of camera_world: its matrix is `C @ view^-1`, and C is its own
    inverse, so `C @ matrix_world` is the engine-space camera. Its -Z column is
    where it looks; pitch and yaw come off that direction alone, which is what
    makes a ROLLED camera decompose to the same aim -- roll is the one thing a
    Blender camera can do that orbitView cannot, and it is reported rather than
    silently folded into a wrong yaw. `at` is `dist` along the look direction:
    every (at, dist) on that ray is the same view, so dist stays the field's
    and only the far plane depends on which one is written.
    """
    m = C @ obj.matrix_world
    rot = m.to_3x3()
    forward = -rot.col[2].normalized()
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -forward.y))))
    yaw = math.degrees(math.atan2(forward.x, -forward.z))
    eye = m.translation
    at = [eye[k] + forward[k] * dist for k in range(3)]
    aimed = (C @ camera_world(yaw, pitch, dist, at)).to_3x3()
    roll = math.degrees(rot.col[0].normalized().angle(aimed.col[0], 0.0))
    return yaw, pitch, at, roll


_camera_sync = {"busy": False}


def sync_camera_fields(context):
    """Fields <- the PC2_Camera object, so a camera moved BY HAND is what exports.

    Writes only the fields that differ, under a guard so _camera_changed does
    not re-aim the camera out from under a drag (that would also snap a rolled
    camera straight, mid-gesture). Returns the roll the engine cannot carry.
    """
    props = context.scene.pc2_entity
    obj = bpy.data.objects.get(CAMERA_NAME)
    if obj is None or obj.type != 'CAMERA':
        return 0.0
    if obj.data.sensor_fit != 'VERTICAL':
        obj.data.sensor_fit = 'VERTICAL'   # or angle_y is not the vertical FOV
    yaw, pitch, at, roll = camera_fields(obj, props.cam_dist)
    render = context.scene.render
    changes = {}
    if abs(wrap_degrees(yaw - props.cam_yaw)) > 1e-3:
        changes["cam_yaw"] = yaw
    if abs(pitch - props.cam_pitch) > 1e-3:
        changes["cam_pitch"] = pitch
    if any(abs(a - b) > 1e-4 for a, b in zip(at, props.cam_at)):
        changes["cam_at"] = at
    fov = math.degrees(obj.data.angle_y)
    if abs(fov - props.cam_fov) > 1e-3:
        changes["cam_fov"] = fov
    if (render.resolution_x, render.resolution_y) != (props.cam_res_x, props.cam_res_y):
        changes["cam_res_x"], changes["cam_res_y"] = render.resolution_x, render.resolution_y
    if changes:
        _camera_sync["busy"] = True
        try:
            for key, value in changes.items():
                setattr(props, key, value)
        finally:
            _camera_sync["busy"] = False
    return roll


def camera_tick():
    """Poll the camera the user is moving; half a second is under a glance."""
    scene = getattr(bpy.context, "scene", None)
    if scene is not None and hasattr(scene, "pc2_entity"):
        try:
            sync_camera_fields(bpy.context)
        except (ValueError, RuntimeError, AttributeError):
            pass
    return 0.5


def stage_entries(coll):
    """(entries, sheared) for one stage collection, ready for stage_block."""
    entries, sheared = [], set()
    for obj in placements_of(coll):
        pos, rot, scale, exact = decompose_matrix(conjugate(placement_matrix(obj)))
        if not exact:
            sheared.add(obj.instance_collection.name)
        entry = {"entity": obj.instance_collection.name}
        emit_trs(entry, pos, rot, scale)
        color = obj.get(COLOR_PROP)
        if color is not None and int(color) >= 0:
            entry["color"] = int(color) & 15
        bank = obj.get(BANK_PROP)
        if bank:
            entry["bank"] = int(bank)
        entries.append(entry)
    return entries, sorted(sheared)


def stage_block(name, entries):
    lines = ["export const %s = [" % name]
    for entry in entries:
        fields = ["e: %s" % entry["entity"]]
        for short, key in (("p", "pos"), ("r", "rot"), ("s", "scale")):
            if key in entry:
                fields.append("%s: [%s]" % (short, ", ".join(entry[key])))
        if "color" in entry:
            fields.append("c: %d" % entry["color"])
        # Bank 0 is the default and never written, so a stage that uses no banks
        # round-trips byte-identical to one authored before banks existed.
        if entry.get("bank"):
            fields.append("b: %d" % entry["bank"])
        lines.append("  { %s }," % ", ".join(fields))
    lines.append("];")
    return "\n".join(lines)


def stages_module(existing, updated):
    """Merge exported stage blocks into stages.js.

    Blocks are kept whole and in the order the file already has them, so a
    stage this .blend has never opened survives untouched and re-exporting one
    stage cannot reshuffle another. The import line is the one thing that
    cannot be merged block by block the way entities.js is -- there is only one
    of it -- so it is rebuilt from the union of every `e:` in every block.
    """
    order, blocks = [], {}
    for match in BLOCK_RE.finditer(existing or ""):
        order.append(match.group(1))
        blocks[match.group(1)] = match.group(0)
    for name in sorted(updated):
        if name not in blocks:
            order.append(name)
        blocks[name] = updated[name]

    # Everything the file opens with is its own: prose, and any import the
    # header explains. Cut at whichever comes first, the import line or the
    # first block.
    cuts = [m.start() for m in (STAGE_IMPORT_RE.search(existing or ""),
                                BLOCK_RE.search(existing or "")) if m]
    header = (existing[:min(cuts)] if cuts else "").rstrip("\n")
    if not header.strip():
        header = DEFAULT_STAGE_HEADER.rstrip("\n")

    lines = [header, ""]
    used = sorted({n for block in blocks.values() for n in USES_RE.findall(block)})
    if used:
        lines += ["import { %s } from './entities.js';" % ", ".join(used), ""]
    for name in order:
        lines += [blocks[name], ""]
    return "\n".join(lines)


# --- the primitive library, loaded without being asked ---------------------

_autoload = {"tried": False, "error": "", "force": False}


def importer_available():
    return hasattr(bpy.ops.import_scene, "picocad2_rewrite")


def _load_primitives_now():
    """Timer body, so a one-shot return of None unregisters it.

    `bpy.ops` raises when an operator reports an error, and a timer has no
    window to report into -- so the reason is kept for the panel to show
    instead of vanishing.
    """
    if _autoload["force"] or not primitive_objects():
        _autoload["force"] = False
        try:
            bpy.ops.pc2.load_primitives()
        except Exception as exc:
            _autoload["error"] = str(exc).strip()
    return None


def autoload_primitives():
    """Schedule the load, once.

    Driven from the panel rather than from register(): the addon is enabled
    during Blender's startup, well before there is a scene to import into, and
    a drawn panel is the first moment anyone wants the primitives. draw() may
    not run operators, hence the zero-delay timer.
    """
    if bpy.app.background:
        return  # headless runs (the tests) call the operator themselves
    if _autoload["tried"] and not _autoload["force"]:
        return
    _autoload["tried"] = True
    bpy.app.timers.register(_load_primitives_now, first_interval=0.0)


@persistent
def _on_blend_loaded(_):
    """A different .blend gets its own primitives, and its states get parents."""
    _autoload.update(tried=False, error="", force=False)
    stamp_state_parents()
    # Placements saved before the tint existed carry Blender's DEFAULT object
    # colour, which is opaque WHITE -- and the tint reads alpha as "an override
    # is in effect", so every one of them would draw white until something
    # touched it. Stamping on load is the same reasoning as
    # stamp_state_parents(): the truth is only knowable once, at open.
    colors = scene_palette(bpy.context)
    for obj in bpy.data.objects:
        sync_instance_color(obj, colors)
    from . import game_preview
    game_preview.reset()
    if bpy.context.scene.pc2_entity.game_preview:
        _game_preview_changed(bpy.context.scene.pc2_entity, bpy.context)


def stamp_state_parents():
    """Record today's tree in any snapshot written before parents were stored.

    This has to happen at FILE LOAD, not lazily. A snapshot with no "p" can
    only assume the hierarchy has not moved since it was captured, so the
    assumption is true exactly once: before the first re-parent of the session.
    Stamping later would record the NEW tree as the one the poses were captured
    in, and the drift Sync exists to fix would look like no drift at all.
    """
    for coll in bpy.data.collections:
        if not coll.get(STATES_PROP):
            continue
        try:
            data = state_data(coll)
        except ValueError:
            continue           # hand-edited JSON: leave it for the panel to report
        parents = current_parents(ordered_parts(coll))
        stamped = False
        for snapshot in [data["base"]] + [s.get("parts") or {} for s in data["states"]]:
            for uid, item in snapshot.items():
                if "p" not in item:
                    item["p"] = parents.get(uid, "")
                    stamped = True
        # Same once-only reasoning for the posed flag: a state saved before it
        # existed can only be compared against the base as stored now, which
        # is right unless the base moved after the state was saved.
        for saved in data["states"]:
            for uid, item in (saved.get("parts") or {}).items():
                if "d" not in item:
                    item["d"] = entry_posed(item, data["base"].get(uid))
                    stamped = True
        if stamped:
            save_state_data(coll, data)


# --- colour: the model's palette, as swatches -------------------------------
#
# A part's `color` is a palette INDEX, and engine.js draws an instance with one
# as a flat solid -- no texture lookup at all. So the swatches have to come from
# the model itself, and the viewport preview has to be flat too.

_palette = {"path": None, "mtime": None, "colors": ()}


def model_palette(path):
    """The model's 16 colours as sRGB float triples, cached on mtime.

    Straight out of model.txt's `texture.colors`, which is the same array
    pico.js feeds to buildPalette -- so a swatch is the colour the engine
    draws, unless the stage swaps in a mood palette at runtime.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ()
    if _palette["path"] != path or _palette["mtime"] != mtime:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)["texture"]["colors"]
            colors = tuple(tuple(float(c) for c in rgb[:3]) for rgb in raw[:16])
        except Exception:
            colors = ()
        _palette.update(path=path, mtime=mtime, colors=colors)
    return _palette["colors"]


# --- the moods the GAME swaps in, read out of main.js ----------------------
#
# A stage's palette is not the model's: loadStage calls E.setPalette with one
# of main.js's PALETTES, indexed by the stage's position in STAGES, and an
# empty entry means "keep the model's own". Both are plain literals, so this is
# a scan rather than a parse -- and it fails SOFT, because it is reading
# hand-written game code that owes this addon nothing: an unreadable main.js
# just means the option resolves to the model's palette.

STAGES_LIST_RE = re.compile(r"^const STAGES = \[([^\]]*)\];", re.M)
BANKS_LIST_RE = re.compile(r"^const PALETTE_BANKS = \[(.*?)\];", re.S | re.M)
PALETTES_LIST_RE = re.compile(r"^const PALETTES = \[(.*?)^\];", re.S | re.M)
BANK_REF_RE = re.compile(r"PALETTES\[(\d+)\]")
_moods = {}


def unhex_palette(text):
    """96 (or 128) hex chars -> 16 (r, g, b) floats, or None for anything else.

    '' is a real value in both arrays and means "keep the model's own", so the
    length check is the whole validation and None is the honest answer.
    """
    # 128 is a scheme carrying its own shade ramp (32 nibbles after the
    # colours); the preview only wants the colours, so the tail is ignored.
    if len(text) not in (96, 128):
        return None
    return tuple(tuple(int(text[i + c * 2:i + c * 2 + 2], 16) / 255.0 for c in range(3))
                 for i in range(0, 96, 6))


def palette_hex(colors):
    """16 (r, g, b) floats -> the 96 hex chars main.js's PALETTES holds.

    The exact inverse of unhex_palette, and the exact shape mood() parses:
    `p.match(/../g).map(h => parseInt(h, 16))` is 48 bytes, which buildPalette
    reads as 16 triples. Rounds the way the runtime already does -- pico.js
    quantises a colour with Math.round(v * 255) -- so a palette copied out of
    here and pasted into main.js is the palette the engine draws.
    """
    return "".join("%02x%02x%02x" % tuple(
        min(255, max(0, int(round(c * 255)))) for c in rgb) for rgb in colors)


def scan_main_js(path):
    """(moods by stage name, one entry per palette BANK above 0) out of main.js.

    Two answers from one read, because the panel asks for both on every redraw
    and main.js is the only place either is written -- restating a mood or a
    bank count here is how the preview would come to disagree with the game.
    Each half parses in its OWN try: main.js need not have PALETTE_BANKS at all
    (it did not, until banks landed), and a file missing one must still give up
    the other. Both fail SOFT -- an unreadable main.js means no moods and no
    banks, so the dropdown quietly falls back to the model's own palette.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}, []
    cached = _moods.get(path)
    if cached is None or cached[0] != mtime:
        moods, banks, hexes = {}, [], []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
        except Exception:
            source = ""
        try:
            names = [n.strip() for n in
                     STAGES_LIST_RE.search(source).group(1).split(",") if n.strip()]
            hexes = re.findall(r"'([0-9a-fA-F]*)'",
                               PALETTES_LIST_RE.search(source).group(1))
            for name, text in zip(names, hexes):
                colors = unhex_palette(text)
                if colors:
                    moods[name] = colors
        except Exception:
            moods = {}
        try:
            for token in BANKS_LIST_RE.search(source).group(1).split(","):
                token = token.strip()
                if not token:
                    continue
                # A bank entry is usually a REFERENCE into PALETTES (reusing a
                # mood costs no data), but a literal 96-char string is just as
                # valid -- so resolve the reference, fall through to a literal,
                # and hand back None for anything else rather than guessing. A
                # bank that resolves to nothing still occupies its slot, or
                # every later bank would shift by one.
                ref = BANK_REF_RE.fullmatch(token)
                if ref and int(ref.group(1)) < len(hexes):
                    banks.append(unhex_palette(hexes[int(ref.group(1))]))
                else:
                    literal = re.fullmatch(r"'([0-9a-fA-F]*)'", token)
                    banks.append(unhex_palette(literal.group(1)) if literal else None)
        except Exception:
            banks = []
        cached = _moods[path] = (mtime, moods, banks)
    return cached[1], cached[2]


def game_palettes(path):
    """stage NAME -> its 16 mood colours, for the stages that swap one in."""
    return scan_main_js(path)[0]


def game_bank_palettes(path):
    """One entry per palette bank ABOVE 0, each 16 colours or None."""
    return scan_main_js(path)[1]


def game_banks(path):
    """How many palette banks main.js defines, bank 0 included.

    Scanned rather than restated: a number typed here could offer a bank the
    engine has nothing loaded in, and the panel would then write a `b:` that
    draws in whatever the palette texture happens to hold.
    """
    return 1 + len(game_bank_palettes(path))


def palette_hex(colors):
    """16 (r, g, b) floats -> the 96 hex chars main.js's PALETTES holds.

    The inverse of what mood() does at runtime: `p.match(/../g).map(h =>
    parseInt(h, 16))` is 48 bytes, which buildPalette reads as 16 triples.
    Rounds the way the runtime already does -- pico.js quantises a colour with
    Math.round(v * 255) -- so a palette copied out of here is the palette the
    engine draws.
    """
    return "".join("%02x%02x%02x" % tuple(
        min(255, max(0, int(round(c * 255)))) for c in rgb) for rgb in colors)


def main_js_path(props):
    """main.js sits beside the generated modules, so it needs no field of its
    own -- and a path that cannot drift from stages_path cannot be half-set."""
    return os.path.join(os.path.dirname(bpy.path.abspath(props.stages_path)), "main.js")


PALETTE_MODEL = 'MODEL'   # the model's own colours -- what the engine draws
PALETTE_STAGE = 'STAGE'   # the active stage's mood swap, from main.js
PALETTE_BANK = 'BANK%d'   # a palette BANK, for a placement wearing `b: N`

# How many bank entries the dropdown offers. The real ceiling is engine.js's
# BANKS, and it cannot be read from there: this items list is built once at
# import (see below), long before a scene names a path to read one out of. So
# it is a ceiling on the UI, not on the engine -- a bank main.js does not
# define says so and falls back to the model's palette, which is what makes
# over-declaring here harmless and under-declaring the only real mistake.
PALETTE_BANK_SLOTS = 3


def bank_choice(chosen):
    """The bank number a dropdown id names, or None if it names something else.

    The one place the id's SHAPE is known: scene_palette and the View panel
    both ask this instead of slicing the string themselves, so PALETTE_BANK's
    format and the readers cannot drift apart.
    """
    for n in range(1, PALETTE_BANK_SLOTS + 1):
        if chosen == PALETTE_BANK % n:
            return n
    return None

# The library, in the two shapes the UI needs. Both are built once at import:
# Blender does not own the strings an enum callback hands back, so an items
# list rebuilt per draw can be collected out from under the dropdown.
PALETTE_ITEMS = ((PALETTE_MODEL, "Model palette", "model.txt's own colours -- "
                  "what the engine actually draws"),
                 (PALETTE_STAGE, "This stage's mood",
                  "The palette loadStage swaps in for the ACTIVE stage (main.js's "
                  "PALETTES). Stages that swap nothing fall back to the model's own"),
                 ) + tuple(
    (PALETTE_BANK % n, "Palette bank %d" % n,
     "The colours a placement marked `b: %d` resolves its indices through "
     "(main.js's PALETTE_BANKS). The viewport wears ONE palette at a time, so "
     "this shows the whole scene in that bank's colours" % n)
    for n in range(1, PALETTE_BANK_SLOTS + 1)) + tuple(
    (p["id"], p["name"], "%s (%s)" % (p["url"], p["author"] or "unknown"))
    for p in PALETTE_LIBRARY)
PALETTE_COLORS = {p["id"]: tuple(tuple(c / 255.0 for c in rgb) for rgb in p["colors"])
                  for p in PALETTE_LIBRARY}


def scene_palette(context):
    """The 16 colours the swatches and the flat-colour preview show.

    Normally the model's own, because a swatch should be the colour the engine
    draws. The dropdown swaps in a library palette to TRY an entity under
    another mood: a part stores a palette INDEX either way, so nothing about
    the blueprint changes when this does, and model.txt is not touched.
    """
    props = context.scene.pc2_entity
    chosen = props.palette
    if chosen == PALETTE_STAGE and props.stage is not None:
        # A stage with no entry in PALETTES keeps the model's own -- exactly
        # what loadStage does with an empty string, so falling through here IS
        # the game's behaviour rather than a fallback for a missing feature.
        mood = game_palettes(main_js_path(props)).get(props.stage.name)
        if mood:
            return mood
    elif bank_choice(chosen):
        # A bank the file does not define, or one holding '', falls through to
        # the model's palette -- the same shape as a stage that swaps nothing.
        banks = game_bank_palettes(main_js_path(props))
        index = bank_choice(chosen) - 1
        if index < len(banks) and banks[index]:
            return banks[index]
    elif chosen in PALETTE_COLORS:
        return PALETTE_COLORS[chosen]
    return model_palette(bpy.path.abspath(props.model_path))


_swatches = {"pcoll": None, "key": None}


CELL = 16          # texels per grid cell -- fixed, so the atlas keeps one size
CELL_ZOOM = 2      # nearest-neighbour, so the pixel art survives button size
TILE_CELL = 0.75   # button width per icon; 1.0 leaves gaps between tiles
# How a swatch or a tile says whether it is IN EFFECT or merely remembered:
# Blender's own `depress` highlight is one theme colour and cannot say both,
# and these icon buffers are ours to draw into anyway. Both borders are drawn
# by stamp_rect_border, further down -- a swatch is just the whole 16x16 icon
# as the rect.
IN_EFFECT = (0.11, 0.85, 0.16, 1.0)   # green: this is what the engine draws
REMEMBERED = (0.58, 0.58, 0.62, 1.0)  # grey: picked, but the other mode won


def palette_icons(colors, marked, in_effect):
    """A tiny solid-colour icon per palette slot, so the swatch buttons show
    the colour instead of a number, with a border on the chosen one.

    Previews need a UI, so this returns None headless (and on any failure) and
    the panel falls back to numbered buttons.
    """
    if not colors:
        return None
    key = (tuple(colors), marked, in_effect)
    if _swatches["key"] == key and _swatches["pcoll"] is not None:
        return _swatches["pcoll"]
    pcoll = _swatches["pcoll"]
    try:
        import bpy.utils.previews
        if pcoll is None:
            pcoll = bpy.utils.previews.new()
            for i in range(len(colors)):
                pcoll.new("pal%d" % i)
        for i, rgb in enumerate(colors):
            preview = pcoll["pal%d" % i]
            data = (list(rgb) + [1.0]) * (16 * 16)
            if i == marked:
                stamp_rect_border(data, 16, 16, (0, 0, 16, 16),
                                  IN_EFFECT if in_effect else REMEMBERED)
            preview.icon_size = [16, 16]
            preview.icon_pixels_float = data
        if not pcoll["pal0"].icon_id:
            raise RuntimeError("no icon ids -- no UI")
    except Exception:
        drop_palette_icons()
        return None
    _swatches.update(pcoll=pcoll, key=key)
    return pcoll


def drop_palette_icons():
    if _swatches["pcoll"] is not None:
        try:
            import bpy.utils.previews
            bpy.utils.previews.remove(_swatches["pcoll"])
        except Exception:
            pass
    _swatches.update(pcoll=None, key=None)


def srgb_to_linear(c):
    """Blender's colour inputs are linear; model.txt's palette is sRGB."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def placement_tint(mat):
    """Let a coloured stage PLACEMENT override whatever a part draws.

    A placement instances the entity's collection, and every other placement of
    that entity shares it -- so a placement's colour cannot live in a material
    slot the way a part's does. What IS per-instance is the OBJECT COLOUR:
    Blender's Object Info node reads the INSTANCER's colour for instanced
    geometry and the object's own otherwise, so ONE shared material serves every
    placement of an entity.

    ALPHA is the flag, because a colour and "no colour" are both RGB: alpha 1
    means an override is in effect and the mix takes it WHOLE, alpha 0 leaves
    the part exactly as it drew before. Taking it whole is the engine's
    semantic, not a choice -- drawEntity is handed one colour for the entire
    entity, so a placement colour REPLACES each part's own rather than tinting
    it. Hence also sync_instance_color's insistence on alpha 0: Blender's
    default object colour is opaque WHITE, which this would otherwise paint
    over every part in the scene.

    Idempotent, and safe on a material whose emission was rewired after the mix
    was inserted (game_preview copies a tinted material and then links its own
    palette lookup in): what is checked is the LINK, not just the node.
    """
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return mat
    emission = next((n for n in nt.nodes if n.type == 'EMISSION'), None)
    if emission is None:
        return mat
    socket = emission.inputs["Color"]
    mix = nt.nodes.get(TINT_MIX)
    # By NAME, never by identity: Blender hands back a fresh Python wrapper on
    # every access, so `from_node is mix` is False for the very same node --
    # which made this run twice and wire the mix's output into its own A input.
    # A cycle renders BLACK, and color_material repaints on every palette
    # change, so one stale comparison blackened every coloured part on load.
    if any(l.to_socket == socket and l.from_node.name == TINT_MIX for l in nt.links):
        return mat
    # Whatever feeds the emission now is the base the placement overrides. A
    # flat colour has no link at all and lives in the input's own value, which
    # a link would shadow -- so it moves into a node of its own, and
    # flat_color_target() is where color_material then writes it. The mix is
    # excluded explicitly: belt and braces, so no ordering can build a cycle.
    base = next((l.from_socket for l in nt.links
                 if l.to_socket == socket and l.from_node.name != TINT_MIX), None)
    if base is None:
        rgb = nt.nodes.get(TINT_BASE)
        if rgb is None:
            rgb = nt.nodes.new("ShaderNodeRGB")
            rgb.name = rgb.label = TINT_BASE
            rgb.location = (emission.location.x - 420, emission.location.y - 120)
            rgb.outputs[0].default_value = socket.default_value[:]
        base = rgb.outputs[0]
    if mix is None:
        mix = nt.nodes.new("ShaderNodeMix")
        mix.name = mix.label = TINT_MIX
        mix.data_type = 'RGBA'
        mix.location = (emission.location.x - 200, emission.location.y - 120)
    info = nt.nodes.get(TINT_INFO)
    if info is None:
        info = nt.nodes.new("ShaderNodeObjectInfo")
        info.name = info.label = TINT_INFO
        info.location = (emission.location.x - 420, emission.location.y - 360)
    # ShaderNodeMix carries one socket pair per data type and names them all
    # "A"/"B", so the RGBA pair is reached by index: 6, 7 in, 2 out.
    nt.links.new(info.outputs["Alpha"], mix.inputs["Factor"])
    nt.links.new(base, mix.inputs[6])
    nt.links.new(info.outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], socket)
    return mat


def flat_color_target(mat):
    """Where a flat material's colour is written.

    The emission input, unless placement_tint has moved it into a node of its
    own -- one definition, so a repaint cannot write somewhere the graph no
    longer reads.
    """
    nt = mat.node_tree
    node = nt.nodes.get(TINT_BASE)
    if node is not None:
        return node.outputs[0]
    return next(n for n in nt.nodes if n.type == 'EMISSION').inputs["Color"]


def sync_instance_color(obj, colors=None):
    """Carry a placement's colour override in its OBJECT colour.

    The only per-instance channel a collection instance has; placement_tint is
    the other half. Alpha 0 rather than absent, because Blender's default is
    opaque white and the tint would take it.
    """
    if not is_placement(obj):
        return
    index = part_color(obj)
    if index < 0:
        obj.color = (1.0, 1.0, 1.0, 0.0)
        return
    if colors is None:
        colors = scene_palette(bpy.context)
    index &= 15
    rgb = colors[index] if index < len(colors) else (1.0, 0.0, 1.0)
    obj.color = tuple(srgb_to_linear(c) for c in rgb) + (1.0,)


def color_material(index, colors):
    """A flat material for one palette slot.

    Created here rather than found, because the picoCAD2 importer only emits a
    `*_color_unlit_N` material for colours the model's faces actually use --
    this model has none at all, every face is textured. The name still matches
    COLOR_MAT_RE so color_of() reads it back the same way.

    ONE Emission node, which is exactly the graph the importer builds for a
    colour it DOES emit -- so every part in the scene is lit the same way,
    which is to say not at all. The engine draws a colour override as a flat
    solid (no texture lookup, and the dither bands are its only shading), so a
    preview that takes a highlight or falls off with the surface normal is
    showing light the game does not have. A Principled BSDF was doing that
    until Sep 2026: it responds to the studio light whatever its emission is
    set to, which is where the gradient across a hemisphere came from.

    `diffuse_color` carries the same colour again for SOLID viewport shading,
    which ignores node graphs entirely -- see PC2_OT_flat_preview for the other
    half of that, since flatness in Solid mode is a VIEWPORT setting, not a
    material one.
    """
    name = "pc2_color_unlit_%d" % index
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    # Tinted on every call rather than only on creation: the material is named
    # by palette SLOT, not by colour, so a palette swap has to repaint it.
    rgb = colors[index] if index < len(colors) else (1.0, 0.0, 1.0)
    linear = tuple(srgb_to_linear(c) for c in rgb)
    mat.diffuse_color = linear + (1.0,)   # Solid shading
    node_tree = mat.node_tree
    emission = next((n for n in node_tree.nodes if n.type == 'EMISSION'), None)
    if emission is None:
        # Nothing to salvage: either a fresh material, or a Principled one this
        # addon made before it knew better. Rebuilding is also the upgrade path.
        node_tree.nodes.clear()
        out = node_tree.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        emission = node_tree.nodes.new("ShaderNodeEmission")
        node_tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    placement_tint(mat)
    flat_color_target(mat).default_value = linear + (1.0,)
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False   # the engine draws faces double-sided
    return mat


def retint_color_materials(colors):
    """Repaint the flat-colour materials that already exist.

    Walks MATERIALS rather than objects because one of them is shared by every
    part using that slot, so re-tinting sixteen recolours the whole scene.
    """
    for index in range(16):
        if bpy.data.materials.get("pc2_color_unlit_%d" % index) is not None:
            color_material(index, colors)


def part_color(obj):
    """This part's colour override as an index, or -1 for none."""
    value = obj.get(COLOR_PROP)
    return int(value) if value is not None else -1


def draw_swatches(context, box, current, in_effect):
    """The 16 palette buttons, plus what is set and how to drop it.

    ONE definition, because a part and a whole placement wear the same
    override: the Part panel and the Stage panel would otherwise each have
    their own idea of what a swatch grid looks like.
    """
    icons = palette_icons(scene_palette(context), current, in_effect)
    grid = box.grid_flow(row_major=True, columns=8, even_columns=True, align=True)
    for i in range(16):
        if icons is not None:
            op = grid.operator(PC2_OT_set_color.bl_idname, text="", emboss=False,
                               icon_value=icons["pal%d" % i].icon_id)
        else:
            # No previews (headless, or the model has no palette): the index is
            # all there is to show.
            op = grid.operator(PC2_OT_set_color.bl_idname, text=str(i),
                               depress=(i == current))
        op.index = i
    if current >= 0:
        row = box.row(align=True)
        row.label(text="colour %d%s" % (current, "" if in_effect else " (remembered)"))
        row.operator(PC2_OT_set_color.bl_idname, text="", icon='X').index = -1
    elif in_effect:
        box.label(text="pick a swatch", icon='INFO')

# --- uv: spec <-> properties, and the viewport preview ----------------------

def textured_slot_index(obj):
    """The material slot carrying the atlas (picoCAD2's `*_textured_unlit_mat`)."""
    for i, slot in enumerate(obj.material_slots):
        mat = obj.data.materials[i] if i < len(obj.data.materials) else None
        if mat is not None and mat.name.endswith("_textured_unlit_mat"):
            return i
    return 0 if obj.material_slots else -1


def base_texture_image(obj):
    index = textured_slot_index(obj)
    if index < 0 or index >= len(obj.data.materials):
        return None, None
    mat = obj.data.materials[index]
    if mat is None or not mat.use_nodes:
        return None, None
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            return mat, node.image
    return mat, None


_PIXEL_CACHE = {}
_ALPHA_CACHE = {}


def image_pixels(image):
    """The atlas as a flat RGBA float list, cached.

    `image.pixels` is a slow bpy_prop_array and the panel redraws on every
    mouse move, so it is read once per image. Rows run BOTTOM-UP, Blender's
    order, not the engine's -- every reader flips for itself.
    """
    key = (image.name, tuple(image.size))
    cached = _PIXEL_CACHE.get(key)
    if cached is None:
        cached = list(image.pixels)
        _PIXEL_CACHE[key] = cached
    return cached


def alpha_mask(image):
    """Which texels are drawn at all."""
    key = (image.name, tuple(image.size))
    cached = _ALPHA_CACHE.get(key)
    if cached is None:
        px = image_pixels(image)
        cached = [px[i * 4 + 3] > 0.5 for i in range(image.size[0] * image.size[1])]
        _ALPHA_CACHE[key] = cached
    return cached


_atlas = {"key": None, "pcoll": None, "base": None, "base_key": None,
          "cols": 0, "rows": 0}

CHECKER = ((0.16, 0.16, 0.17, 1.0), (0.22, 0.22, 0.23, 1.0))


def tile_rect(u, v, size, height):
    """A tile's box in IMAGE space -- (x, y, w, h) with y measured bottom-up.

    The one flip in the picker: Blender image and preview buffers run
    bottom-up, while the engine numbers tiles from the TOP (`uv_dest_rect`
    builds its rect from `(v - 1) * size` with V down). Mark a tile with the
    engine's own y and the box lands on the mirrored row -- and still looks
    like a plausible piece of texture. A test walks this against
    tile_occupancy()'s independent top-down count for every tile.
    """
    return (u - 1) * size, height - v * size, size, size


def atlas_base(image):
    """The atlas as a flat bottom-up RGBA list, transparent texels composited
    onto a checker.

    Three quarters of this atlas is the transparent index; left transparent the
    preview would blend into the panel and you could not see where the image
    ends. Cached, so moving the marker only copies a list.
    """
    w, h = image.size[0], image.size[1]
    key = (image.name, w, h)
    if _atlas["base_key"] == key and _atlas["base"] is not None:
        return _atlas["base"]
    px = image_pixels(image)
    data = []
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            a = px[i + 3]
            if a > 0.5:
                data.extend(px[i:i + 4])
            else:
                data.extend(CHECKER[((x >> 3) + (y >> 3)) & 1])
    _atlas.update(base_key=key, base=data)
    return data


def stamp_rect_border(data, w, h, rect, rgba, thickness=2):
    """Outline an atlas-space rectangle in a whole-image buffer.

    Drawn once into a copy of the atlas and then sliced into cells, so no cell
    has to work out which piece of the outline it holds -- and the marker is
    free to be any size, which is the point: the SELECTION resizes, the picture
    does not.
    """
    x0, y0, rw, rh = rect
    for y in range(max(0, y0), min(h, y0 + rh)):
        edge_y = y - y0 < thickness or y0 + rh - y <= thickness
        for x in range(max(0, x0), min(w, x0 + rw)):
            if edge_y or x - x0 < thickness or x0 + rw - x <= thickness:
                i = (y * w + x) * 4
                data[i:i + 4] = list(rgba)


def atlas_cells(image, rect, in_effect):
    """The atlas as a grid of clickable cells, with `rect` outlined.

    A cell is a FIXED CELL texels whatever the tile size is, because a button
    draws its icon at one size whatever the buffer holds: a grid that followed
    the tile size would shrink the whole texture to a quarter at size 32 and to
    a sixteenth at 64. So the cells are the picture and the marker is the
    selection, and they move independently.

    Rebuilt only when the image or the marker changes; returns None when
    previews are unavailable (a headless run), and the panel falls back to the
    U/V fields, which are always on screen anyway.
    """
    w, h = image.size[0], image.size[1]
    if not w or not h:
        return None
    key = (image.name, w, h, rect, in_effect)
    if _atlas["key"] == key and _atlas["pcoll"] is not None:
        return _atlas
    cols, rows = max(1, w // CELL), max(1, h // CELL)
    data = list(atlas_base(image))
    if rect is not None:
        stamp_rect_border(data, w, h, rect, IN_EFFECT if in_effect else REMEMBERED)
    pcoll = _atlas["pcoll"]
    try:
        import bpy.utils.previews
        if pcoll is None or _atlas["cols"] != cols or _atlas["rows"] != rows:
            drop_atlas_icon()
            pcoll = bpy.utils.previews.new()
            for v in range(1, rows + 1):
                for u in range(1, cols + 1):
                    pcoll.new("t%d_%d" % (u, v))
        for v in range(1, rows + 1):
            for u in range(1, cols + 1):
                x0, y0, cw, ch = tile_rect(u, v, CELL, h)
                pixels = []
                for y in range(y0, y0 + ch):
                    start = (y * w + x0) * 4
                    line = []
                    for x in range(cw):
                        line.extend(data[start + x * 4:start + x * 4 + 4] * CELL_ZOOM)
                    pixels.extend(line * CELL_ZOOM)
                preview = pcoll["t%d_%d" % (u, v)]
                preview.icon_size = [cw * CELL_ZOOM, ch * CELL_ZOOM]
                preview.icon_pixels_float = pixels
        if not pcoll["t1_1"].icon_id:
            raise RuntimeError("no icon ids -- no UI")
    except Exception:
        drop_atlas_icon()
        return None
    _atlas.update(key=key, pcoll=pcoll, cols=cols, rows=rows)
    return _atlas


def drop_atlas_icon():
    if _atlas["pcoll"] is not None:
        try:
            import bpy.utils.previews
            bpy.utils.previews.remove(_atlas["pcoll"])
        except Exception:
            pass
    _atlas.update(key=None, pcoll=None, cols=0, rows=0)


def tile_occupancy(obj):
    """Fraction of the chosen tile that is actually drawn, or None.

    Most of this atlas is the transparent index, which the fragment shader
    `discard`s -- so an empty tile makes the part vanish in the GAME too, not
    just in Blender. The marker on the atlas shows an EMPTY tile plainly; this
    puts a number on a tile that is only partly drawn, which does not stand out
    at preview size.

    Deliberately walks the atlas TOP-DOWN, the way the engine numbers tiles,
    with its own flip to Blender's bottom-up rows -- it is the independent
    check tile_rect() is tested against.
    """
    _, image = base_texture_image(obj)
    if image is None or not image.size[0]:
        return None
    w, h = image.size[0], image.size[1]
    mask = alpha_mask(image)
    p = obj.pc2
    size = tile_size(p)
    x0, y0 = (p.uv_tile_u - 1) * size, (p.uv_tile_v - 1) * size  # engine, V down
    if x0 >= w or y0 >= h:
        return 0.0
    filled = total = 0
    for y in range(y0, min(y0 + size, h)):
        row = (h - 1 - y) * w  # engine row -> Blender's bottom-up row
        for x in range(x0, min(x0 + size, w)):
            total += 1
            if mask[row + x]:
                filled += 1
    return (filled / total) if total else 0.0


def active_color(obj):
    """The colour the export should emit, or None.

    The remembered swatch counts only while colour is the mode in effect. A
    colour that came from a MATERIAL rather than from this panel is not ours to
    gate, so the importer's `*_color_unlit_N` fallback still passes through.
    """
    if COLOR_PROP in obj:
        index = part_color(obj)
        return index if (index >= 0 and obj.pc2.draws == 'COLOR') else None
    return color_of(obj)


def uv_spec_of(obj):
    """A part's blueprint `uv`, as JS text, or None.

    A verbatim spec (a `rect`, which neither editor authors) wins, so importing
    and re-exporting one cannot quietly rewrite it.
    """
    if obj.pc2.draws == 'COLOR':
        return None   # a flat instance has no texture lookup to retile
    raw = obj.get(UV_PROP)
    if raw:
        return str(raw)
    p = obj.pc2
    fields = []
    if p.draws == 'TILE':
        tile = ["u: %d" % p.uv_tile_u, "v: %d" % p.uv_tile_v]
        if tile_size(p) != DEFAULT_TILE_SIZE:
            tile.append("size: %d" % tile_size(p))
        fields.append("tile: { %s }" % ", ".join(tile))
    if abs(p.uv_repeat_u - 1.0) > 1e-6:
        fields.append("repeatU: %s" % num(p.uv_repeat_u))
    if abs(p.uv_repeat_v - 1.0) > 1e-6:
        fields.append("repeatV: %s" % num(p.uv_repeat_v))
    return "{ %s }" % ", ".join(fields) if fields else None


@contextmanager
def quiet_uv(obj):
    """Change several UV fields as ONE edit: one preview rebuild at the end,
    not one per field.

    try/finally, not a bare pair of assignments -- an exception in between
    would leave the part permanently muted, its viewport preview silently
    frozen. Yields the props so a caller can spell the fields off it.
    """
    props = obj.pc2
    props.uv_muted = True
    try:
        yield props
    finally:
        props.uv_muted = False
        refresh_part_preview(obj)


def apply_uv_spec(obj, spec):
    """Blueprint `uv` -> part properties. Shapes Blender cannot author (a
    `rect`) are stashed verbatim instead of being dropped or approximated."""
    with quiet_uv(obj) as p:
        if not isinstance(spec, dict) or set(spec) - {"tile", "repeatU", "repeatV"}:
            obj[UV_PROP] = uv_to_js(spec)
            return
        size = str(spec.get("tile", {}).get("size", DEFAULT_TILE_SIZE))
        if size not in TILE_SIZES:
            # A tile size the picker cannot offer: carry the whole spec
            # verbatim rather than snapping it to something else, the same way
            # a `rect` is carried.
            obj[UV_PROP] = uv_to_js(spec)
            return
        obj.pop(UV_PROP, None)
        tile = spec.get("tile")
        if tile:
            p.draws = 'TILE'
            p.uv_tile_u = int(tile.get("u", 1))
            p.uv_tile_v = int(tile.get("v", 1))
            p.uv_tile_size = size
        p.uv_repeat_u = float(spec.get("repeatU", 1.0))
        p.uv_repeat_v = float(spec.get("repeatV", 1.0))


def build_uv_material(obj, base, src, dst, repeat):
    """A copy of the atlas material whose UVs run through the shader's retile.

    Node-for-node the fragment shader's:
        l = fract((t - src.xy) / src.zw * repeat);  return dst.xy + l * dst.zw
    evaluated in ENGINE uv space -- flip V in, flip V out -- so no reasoning
    about Blender's bottom-up images leaks into the math.
    """
    # Blender caps datablock names at 63 chars, so spelling the rects out would
    # truncate away the very numbers that make the key unique -- hash instead.
    spec = "%s|%s|%s" % (tuple(src), tuple(dst), tuple(repeat))
    key = "pc2uv_%s_%s" % (obj.data.name[:24],
                           hashlib.md5(spec.encode()).hexdigest()[:12])
    existing = bpy.data.materials.get(key)
    if existing is not None:
        return existing

    mat = base.copy()
    mat.name = key
    nt = mat.node_tree
    tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE'), None)
    if tex is None:
        return base

    def new(kind, op=None, x=0, y=0):
        node = nt.nodes.new(kind)
        node.location = (tex.location[0] - 1400 + x, tex.location[1] + y)
        if op:
            node.operation = op
        return node

    def vec(node, index, value):
        node.inputs[index].default_value = value

    # Texture Coordinate's UV output is the active UV map with no name to get
    # wrong -- a UV Map node with a blank field is not reliably the same thing.
    uv_map = new('ShaderNodeTexCoord', x=0)
    sep_in = new('ShaderNodeSeparateXYZ', x=170)
    flip_in = new('ShaderNodeMath', 'SUBTRACT', x=330)
    flip_in.inputs[0].default_value = 1.0
    join_in = new('ShaderNodeCombineXYZ', x=490)

    offset = new('ShaderNodeVectorMath', 'SUBTRACT', x=650)
    vec(offset, 1, (src[0], src[1], 0.0))
    norm = new('ShaderNodeVectorMath', 'DIVIDE', x=810)
    vec(norm, 1, (max(src[2], 1e-6), max(src[3], 1e-6), 1.0))
    rep = new('ShaderNodeVectorMath', 'MULTIPLY', x=970)
    vec(rep, 1, (repeat[0], repeat[1], 1.0))
    frac = new('ShaderNodeVectorMath', 'FRACTION', x=1130)
    scale = new('ShaderNodeVectorMath', 'MULTIPLY', x=1290)
    vec(scale, 1, (dst[2], dst[3], 1.0))
    place = new('ShaderNodeVectorMath', 'ADD', x=1450)
    vec(place, 1, (dst[0], dst[1], 0.0))

    sep_out = new('ShaderNodeSeparateXYZ', x=1610)
    flip_out = new('ShaderNodeMath', 'SUBTRACT', x=1770)
    flip_out.inputs[0].default_value = 1.0
    join_out = new('ShaderNodeCombineXYZ', x=1930)

    link = nt.links.new
    link(uv_map.outputs["UV"], sep_in.inputs[0])
    link(sep_in.outputs["Y"], flip_in.inputs[1])
    link(sep_in.outputs["X"], join_in.inputs["X"])
    link(flip_in.outputs[0], join_in.inputs["Y"])
    link(join_in.outputs[0], offset.inputs[0])
    link(offset.outputs[0], norm.inputs[0])
    link(norm.outputs[0], rep.inputs[0])
    link(rep.outputs[0], frac.inputs[0])
    link(frac.outputs[0], scale.inputs[0])
    link(scale.outputs[0], place.inputs[0])
    link(place.outputs[0], sep_out.inputs[0])
    link(sep_out.outputs["Y"], flip_out.inputs[1])
    link(sep_out.outputs["X"], join_out.inputs["X"])
    link(flip_out.outputs[0], join_out.inputs["Y"])
    link(join_out.outputs[0], tex.inputs["Vector"])
    return mat


def refresh_part_preview(obj):
    _flat_part_preview(obj)
    if (obj is not None and bpy.context.scene.pc2_entity.game_preview
            and obj.type == 'MESH' and mesh_name_of(obj)):
        from . import game_preview
        game_preview.apply(__import__(__name__, fromlist=['']), obj)


def _flat_part_preview(obj):
    """Show in the viewport what the ENGINE would draw for this part.

    Both effects ride on OBJECT-linked material slots, because both are
    per-INSTANCE: slots live on the mesh, which every part of that shape
    shares, so a data-linked material would retile or recolour every copy at
    once.

    One function decides between them, because they do not compose -- a colour
    override draws the instance FLAT, with no texture lookup at all, so a
    retile showing under it would be a preview of something the game never
    draws.
    """
    if obj is None or obj.type != 'MESH' or not mesh_name_of(obj):
        return
    if not obj.material_slots:
        return
    # A part is not a placement: its own colour is in its material, so it must
    # not carry the override FLAG -- see placement_tint, and note Blender's
    # default object colour is opaque white.
    obj.color = (1.0, 1.0, 1.0, 0.0)
    color = part_color(obj) if obj.pc2.draws == 'COLOR' else -1
    if color >= 0:
        mat = color_material(color & 15, scene_palette(bpy.context))
        for slot in obj.material_slots:
            slot.link = 'OBJECT'
            slot.material = mat
        obj.update_tag()
        return
    for slot in obj.material_slots:
        slot.link = 'DATA'
        # The atlas material is shared and DATA-linked, which is exactly what a
        # placement's colour has to override -- and build_uv_material copies
        # it, so a retile inherits the mix.
        placement_tint(slot.material)

    index = textured_slot_index(obj)
    if index < 0:
        return
    slot = obj.material_slots[index]
    p = obj.pc2
    active = (p.draws == 'TILE' or abs(p.uv_repeat_u - 1.0) > 1e-6
              or abs(p.uv_repeat_v - 1.0) > 1e-6)
    if not active:
        obj.update_tag()
        return
    base, _ = base_texture_image(obj)
    if base is None:
        return
    tex_w, tex_h = texture_dims(obj)
    src = mesh_uv_src(obj.data)
    dst = uv_dest_rect(p, src, tex_w, tex_h)
    slot.link = 'OBJECT'
    slot.material = build_uv_material(obj, base, src, dst,
                                      (p.uv_repeat_u, p.uv_repeat_v))
    obj.update_tag()


def _uv_changed(self, context):
    if not self.uv_muted:
        refresh_part_preview(self.id_data)


def _uv_size_changed(self, context):
    """A bigger tile means a smaller grid, so pull the chosen tile back inside
    it -- the same clamp the web editor's clampTile does -- and put TILE in
    effect, the way clicking a cell or a swatch does.

    The marker is drawn at the chosen size in every mode, so a size button
    that left the mode alone SAID it had resized the selection while the part
    went on sampling its own patch, and exported no uv at all.
    """
    if self.uv_muted:   # the importer's assignment: it authors nothing
        return
    obj = self.id_data
    with quiet_uv(obj) as p:
        tex_w, tex_h = texture_dims(obj)
        size = tile_size(p)
        p.uv_tile_u = min(max(1, tex_w // size), max(1, p.uv_tile_u))
        p.uv_tile_v = min(max(1, tex_h // size), max(1, p.uv_tile_v))
        p.draws = 'TILE'


class PC2PartProps(PropertyGroup):
    """Per-part UV retile. Mirrors engine.js's `draw(..., uv)` option."""
    uv_muted: BoolProperty(default=False, options={'HIDDEN'})
    draws: EnumProperty(
        name="Draws As",
        description="Which of the three ways this part can draw is IN EFFECT. "
                    "They are mutually exclusive in the engine -- a colour override "
                    "gets no texture lookup at all -- but the one not in effect is "
                    "remembered, not lost, so switching back costs one click",
        items=[
            ('TEXTURE', "Texture", "The mesh's own patch of the atlas"),
            ('TILE', "Tile", "Remapped onto one tile of the atlas"),
            ('COLOR', "Colour", "Flat palette colour, no texture"),
        ],
        default='TEXTURE',
        update=_uv_changed,
    )
    uv_tile_u: IntProperty(
        name="U", default=1, min=1, update=_uv_changed,
        description="Atlas column, 1-indexed")
    uv_tile_v: IntProperty(
        name="V", default=1, min=1, update=_uv_changed,
        description="Atlas row, 1-indexed")
    uv_tile_size: EnumProperty(
        name="Size",
        description="Tile size in texels. The atlas is always drawn at the same "
                    "size -- this resizes the SELECTION, not the picture. "
                    "Picking one puts Tile in effect",
        items=[(s, s, "%s texel tiles" % s) for s in TILE_SIZES],
        default=str(DEFAULT_TILE_SIZE),
        update=_uv_size_changed,
    )
    uv_repeat_u: FloatProperty(
        name="Repeat U", default=1.0, min=0.0, update=_uv_changed,
        description="Tile the source across U. 1 = no repeat")
    uv_repeat_v: FloatProperty(
        name="Repeat V", default=1.0, min=0.0, update=_uv_changed,
        description="Tile the source across V. 1 = no repeat")


# --- properties ------------------------------------------------------------

def _model_path_changed(self, context):
    """Point the library at another model.txt and it reloads itself."""
    _autoload.update(tried=False, error="", force=True)


def apply_palette(context):
    """Repaint everything the palette drives.

    ONE definition, because the colours now change for two reasons: the
    dropdown, and switching stage while the dropdown is showing THAT stage's
    mood. Swatch icons re-derive themselves (their cache is keyed on the
    colours), but the viewport materials are named by slot and do not.
    """
    props = context.scene.pc2_entity
    colors = scene_palette(context)
    retint_color_materials(colors)
    # A placement's colour rides in its object colour rather than in a material
    # named by slot, so it does not re-derive with the swatches.
    for obj in bpy.data.objects:
        sync_instance_color(obj, colors)
    if props.game_preview:
        _game_preview_changed(props, context)


def _palette_changed(self, context):
    apply_palette(context)


def _camera_changed(self, context):
    """Every camera field re-aims the camera, if there is one to re-aim.

    Deciding an aspect or a pitch is a question you answer by LOOKING, so a
    field that needed a button pressed after it would be answering it one blink
    late."""
    if _camera_sync["busy"]:
        return
    if bpy.data.objects.get(CAMERA_NAME):
        place_camera(context)


def _game_preview_changed(self, context):
    from . import game_preview
    for obj in list(bpy.data.objects):
        if mesh_name_of(obj):
            refresh_part_preview(obj)
    if self.game_preview:
        bpy.ops.pc2.flat_preview()
        if not bpy.app.timers.is_registered(game_preview.tick):
            bpy.app.timers.register(game_preview.tick, first_interval=0.1)
        game_preview.tick()


# What a fresh .blend's camera fields start as. The shipped shot when the repo
# is readable; the numbers main.js used before game_view.js existed otherwise.
GAME_VIEW_DEFAULT = read_game_view(os.path.join(REPO, "src", "game_view.js")) or {
    "width": 600, "height": 800, "at": [0.0, 0.0, 0.0],
    "yaw": 0.0, "pitch": 10.0, "dist": 20.0, "fov": 35.0,
}


class PC2EntityProps(PropertyGroup):
    game_preview: BoolProperty(
        name="Game Preview", default=False, update=_game_preview_changed,
        description="Preview the game's palette shade bands and screen-space dithering",
    )
    game_pixel_scale: IntProperty(
        name="Dither Pixel Size", default=4, min=1, max=16,
        description="Viewport pixels per dither cell; match the game's PIXEL_SCALE",
    )
    palette: EnumProperty(
        name="Palette",
        description="Which 16 colours to show a colour override in. PREVIEW only: "
                    "the engine draws model.txt's palette (or a stage's mood swap), "
                    "so this tries an entity under another scheme without editing "
                    "either. A part stores a palette index, never a colour",
        items=PALETTE_ITEMS,
        default=PALETTE_MODEL,
        update=_palette_changed,
    )
    model_path: StringProperty(
        name="Model",
        description="picoCAD2 model.txt the primitive buttons come from",
        subtype='FILE_PATH',
        default=DEFAULT_MODEL,
        update=_model_path_changed,
    )
    entities_path: StringProperty(
        name="Blueprints",
        description="Generated blueprint module (src/entities.js) -- the entity list "
                    "is read from it and Export writes back into it",
        subtype='FILE_PATH',
        default=DEFAULT_ENTITIES,
    )
    entity: PointerProperty(
        name="Entity",
        description="Collection holding this entity's parts. One collection = one blueprint",
        type=bpy.types.Collection,
    )
    stages_path: StringProperty(
        name="Levels",
        description="Generated stage module (src/stages.js) -- the stage list is read "
                    "from it and Export Stage writes back into it",
        subtype='FILE_PATH',
        default=DEFAULT_STAGES,
    )
    stage: PointerProperty(
        name="Stage",
        description="Collection holding this stage's placements. One collection = one level",
        type=bpy.types.Collection,
    )
    # The camera, defaulting to what the GAME has: src/game_view.js is read
    # once at import so a fresh .blend starts on the shipped shot (3:4,
    # 600x800), and "Load Game Camera" re-reads it into a .blend that has
    # drifted. Not drawn as fields any more: the camera OBJECT is what you
    # author (the timer reads it back), so these are its store and its export.
    cam_yaw: FloatProperty(
        name="Yaw", default=GAME_VIEW_DEFAULT["yaw"],
        description="Which side the camera looks from, degrees -- main.js's makeCamera",
        update=_camera_changed,
    )
    cam_pitch: FloatProperty(
        name="Pitch", default=GAME_VIEW_DEFAULT["pitch"],
        description="How far above the target the camera sits, degrees",
        update=_camera_changed,
    )
    cam_fov: FloatProperty(
        name="FOV", default=GAME_VIEW_DEFAULT["fov"], min=1.0, max=170.0,
        description="Vertical field of view -- main.js's perspective(35, ...)",
        update=_camera_changed,
    )
    cam_dist: FloatProperty(
        name="Dist", default=GAME_VIEW_DEFAULT["dist"], min=0.0,
        description="How far back the eye sits. 0 is first person",
        update=_camera_changed,
    )
    cam_at: FloatVectorProperty(
        name="At", size=3, default=tuple(GAME_VIEW_DEFAULT["at"]), subtype='XYZ',
        description="The point the camera looks at, in ENGINE space "
                    "(y up), the same numbers a stage placement is written in",
        update=_camera_changed,
    )
    cam_res_x: IntProperty(
        name="Width", default=int(GAME_VIEW_DEFAULT["width"]), min=16,
        description="Design width exported with the fixed camera; the game fits this aspect inside its window",
        update=_camera_changed,
    )
    cam_res_y: IntProperty(
        name="Height", default=int(GAME_VIEW_DEFAULT["height"]), min=16,
        description="Viewport height the frame is shaped for",
        update=_camera_changed,
    )
    parent_to_active: BoolProperty(
        name="Parent To Active",
        description="Add new parts as a child of the active part, at its origin, "
                    "following its position and rotation but never its scale",
        default=False,
    )


# --- operators -------------------------------------------------------------

class PC2_OT_load_primitives(Operator):
    bl_idname = "pc2.load_primitives"
    bl_label = "Reload Primitives"
    bl_description = ("Re-import model.txt into the primitive library. Runs by itself the "
                      "first time the panel is drawn; click it after editing the model")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.pc2_entity
        path = bpy.path.abspath(props.model_path)
        if not os.path.exists(path):
            self.report({'ERROR'}, "Model not found: %s" % path)
            return {'CANCELLED'}
        if not importer_available():
            self.report({'ERROR'}, "Enable the 'PicoCad2 Blender Tools' addon first")
            return {'CANCELLED'}

        coll = primitives_collection(create=True)
        # Drop the old source objects. Their mesh datablocks survive as long as
        # parts still reference them, so reloading never guts an entity.
        for obj in list(coll.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        previous = activate_collection(context, coll)
        try:
            bpy.ops.import_scene.picocad2_rewrite(filepath=path, import_animation=False)
        except Exception as exc:
            self.report({'ERROR'}, "Import failed: %s" % exc)
            return {'CANCELLED'}
        finally:
            context.view_layer.active_layer_collection = previous

        # Keep the meshes, tag them, discard the graph's empties.
        count = 0
        for obj in list(coll.objects):
            if obj.type != 'MESH':
                continue
            world = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world
            obj[MESH_PROP] = SUFFIX_RE.sub("", obj.name)
            count += 1
        for obj in list(coll.objects):
            if obj.type != 'MESH':
                bpy.data.objects.remove(obj, do_unlink=True)

        coll.hide_viewport = True  # a library, not part of the scene
        _autoload["error"] = ""
        rebound = self.rebind_parts(coll)
        self.report({'INFO'}, "Loaded %d primitives%s"
                    % (count, ", %d part(s) refreshed" % rebound if rebound else ""))
        return {'FINISHED'}

    def rebind_parts(self, coll):
        """Point existing parts at the freshly imported meshes.

        Reloading is how a model.txt edited in picoCAD2 reaches Blender, and
        the import makes NEW datablocks -- so without this every entity carries
        on drawing the geometry it was built with. Exports are unaffected
        either way: a part's mesh NAME comes from MESH_PROP, not the datablock.
        """
        fresh = {str(o[MESH_PROP]): o.data for o in coll.objects if MESH_PROP in o}
        library = set(coll.objects)
        rebound = 0
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or obj in library:
                continue
            mesh = fresh.get(mesh_name_of(obj) or "")
            if mesh is None or obj.data is mesh:
                continue
            obj.data = mesh
            refresh_part_preview(obj)  # a new mesh means new material slots
            rebound += 1
        return rebound


class _PC2_OT_new_collection(Operator):
    """Shared body for New Entity / New Stage.

    One collection = one exported block, for both, so creating one is the same
    operator twice over: same dialog, same two collision checks, same "show it
    alone and make it the target". Not registered: a subclass supplies the
    bl_idname, the `name` property and the four class attributes below.
    """
    bl_options = {'REGISTER', 'UNDO'}

    kind = "Entity"                # noun for the error messages
    example = "ROCKET"             # a valid name to suggest
    path_prop = "entities_path"    # the module a name would collide in
    hint = "click a primitive to add a part"

    focus = staticmethod(focus_entity)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip()
        if not IDENT_RE.match(name):
            self.report({'ERROR'}, "%s name must be identifier-like, e.g. %s"
                        % (self.kind, self.example))
            return {'CANCELLED'}
        # Both checks stop a silent clobber. Blender would name a second
        # collection NAME.001 and export it under the wrong name; and an export
        # replaces whatever block the module already holds under that name --
        # which is right when you PICKED it from the list, and wrong when you
        # typed a name that happened to be taken.
        if bpy.data.collections.get(name) is not None:
            self.report({'ERROR'}, "'%s' is already in this .blend -- pick it from the list"
                        % name)
            return {'CANCELLED'}
        path = bpy.path.abspath(getattr(context.scene.pc2_entity, self.path_prop))
        if name in file_block_names(path):
            self.report({'ERROR'}, "'%s' is already in %s -- pick it from the list to edit it"
                        % (name, os.path.basename(path)))
            return {'CANCELLED'}
        self.focus(context, bpy.data.collections.new(name))
        self.report({'INFO'}, "Editing %s -- %s" % (name, self.hint))
        return {'FINISHED'}


class PC2_OT_new_entity(_PC2_OT_new_collection):
    bl_idname = "pc2.new_entity"
    bl_label = "New Entity"
    bl_description = "Create an empty entity and start editing it"

    name: StringProperty(name="Name", default="MY_ENTITY")


class PC2_OT_new_stage(_PC2_OT_new_collection):
    bl_idname = "pc2.new_stage"
    bl_label = "New Stage"
    bl_description = "Create an empty level and start placing entities in it"

    name: StringProperty(name="Name", default="STAGE_2")

    kind, example, path_prop = "Stage", "STAGE_2", "stages_path"
    hint = "click an entity to place it"
    focus = staticmethod(focus_stage)


class PC2_OT_edit_entity(Operator):
    bl_idname = "pc2.edit_entity"
    bl_label = "Edit Entity"
    bl_description = ("Show this entity on its own and make it the one parts are added to, "
                      "reading it out of entities.js first if this .blend has not got it")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        coll = bpy.data.collections.get(self.name)
        imported = None
        if coll is None:
            try:
                coll, imported = build_entity_collection(context, self.name)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
        focus_entity(context, coll)
        self.report({'INFO'}, "Editing %s%s"
                    % (self.name,
                       " (imported %d part(s))" % imported if imported is not None else ""))
        return {'FINISHED'}


class PC2_OT_edit_stage(Operator):
    bl_idname = "pc2.edit_stage"
    bl_label = "Edit Stage"
    bl_description = ("Show this level on its own and make it the one placements go into, "
                      "reading it out of stages.js first if this .blend has not got it")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        coll = bpy.data.collections.get(self.name)
        imported = None
        if coll is not None and not coll.get(STAGE_PROP) and any(is_part(o)
                                                                 for o in coll.all_objects):
            self.report({'ERROR'}, "'%s' is an entity in this .blend, not a stage" % self.name)
            return {'CANCELLED'}
        if coll is None:
            try:
                coll, imported = build_stage_collection(context, self.name)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
        focus_stage(context, coll)
        self.report({'INFO'}, "Editing %s%s"
                    % (self.name,
                       " (imported %d placement(s))" % imported if imported is not None else ""))
        return {'FINISHED'}


class PC2_OT_place_entity(Operator):
    bl_idname = "pc2.place_entity"
    bl_label = "Place"
    bl_description = ("Drop an instance of this entity into the stage at the 3D cursor, "
                      "reading it out of entities.js first if this .blend has not got it")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        stage = context.scene.pc2_entity.stage
        if stage is None:
            self.report({'ERROR'}, "Pick or create a stage first")
            return {'CANCELLED'}
        source = bpy.data.collections.get(self.name)
        note = ""
        if source is None:
            try:
                source, count = build_entity_collection(context, self.name)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            source.use_fake_user = True
            note = " (imported %d part(s))" % count
        # The source must be out of the scene while its instances are in it, or
        # every placement is shadowed by one extra copy sitting at the origin.
        show_only(context, stage)
        order = int(max([float(o.get(ORDER_PROP, 0)) for o in placements_of(stage)]
                        + [-1.0])) + 1
        empty = new_placement(source, None, order, Matrix.Identity(4))
        stage.objects.link(empty)
        empty.location = context.scene.cursor.location

        for other in context.selected_objects:
            other.select_set(False)
        empty.select_set(True)
        context.view_layer.objects.active = empty
        self.report({'INFO'}, "Placed %s in %s%s" % (self.name, stage.name, note))
        return {'FINISHED'}


class PC2_OT_sign_guide(Operator):
    bl_idname = "pc2.sign_guide"
    bl_label = "Add / Reset Sign Guide"
    bl_description = "Approximate sign outline for this camera and aspect; PLACEMENT helpers never export"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.stage
        if coll is None:
            return {'CANCELLED'}
        sign_guide(context, coll)
        return {'FINISHED'}


class PC2_OT_stage_camera(Operator):
    bl_idname = "pc2.stage_camera"
    bl_label = "Game Camera"
    bl_description = ("Put the game's camera in the scene and look through it. One camera "
                      "for every level; move it by hand and Export Stage writes it")
    bl_options = {'REGISTER', 'UNDO'}

    look: BoolProperty(default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        info = place_camera(context)
        if info is None:
            self.report({'ERROR'}, "Pick a stage first")
            return {'CANCELLED'}
        if self.look:
            # Camera view is where the passepartout -- the frame -- is drawn at
            # all, so a button called "look through it" has to actually look.
            for area in getattr(context.screen, "areas", ()):
                if area.type == 'VIEW_3D':
                    area.spaces[0].region_3d.view_perspective = 'CAMERA'
        self.report({'INFO'}, "Game camera at %.2f, %.2f, %.2f, dist %.2f -- move it by hand"
                    % (*info["at"], info["dist"]))
        return {'FINISHED'}


# Shapes worth deciding between, not a list of devices: the game fills its
# window, so the only real question is whether a level is composed wide or
# tall. The pixel counts are just a comfortable size at each shape -- what
# reaches the frame is the RATIO.
ASPECTS = (("16:9", 960, 540), ("4:3", 800, 600), ("3:4", 600, 800), ("9:16", 540, 960))


class PC2_OT_camera_aspect(Operator):
    bl_idname = "pc2.camera_aspect"
    bl_label = "Aspect"
    bl_description = "Shape the frame for this ratio"
    bl_options = {'REGISTER', 'UNDO'}

    width: IntProperty(default=960)
    height: IntProperty(default=540)

    def execute(self, context):
        props = context.scene.pc2_entity
        props.cam_res_x, props.cam_res_y = self.width, self.height
        return {'FINISHED'}


class PC2_OT_load_game_view(Operator):
    bl_idname = "pc2.load_game_view"
    bl_label = "Load Game Camera"
    bl_description = ("Read src/game_view.js -- the camera the game actually runs -- into "
                      "these fields and re-aim the Blender camera to it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.pc2_entity
        path = game_view_path(props)
        view = read_game_view(path)
        if view is None:
            self.report({'ERROR'}, "No GAME_VIEW in %s" % path)
            return {'CANCELLED'}
        apply_game_view(context, view)
        self.report({'INFO'}, "Camera loaded from game_view.js: %dx%d, at %s, dist %s"
                    % (props.cam_res_x, props.cam_res_y,
                       ", ".join(num(v) for v in props.cam_at), num(props.cam_dist)))
        return {'FINISHED'}


def game_view_module(props):
    """One fixed camera shared by every stage, in the runtime's convention."""
    return ("// Shared game framing. Blender's Export Stage writes this in Fixed camera mode.\n"
            "// One camera for every stage; width/height are design pixels, fov is vertical.\n"
            "export const GAME_VIEW = {\n"
            "  width: %d, height: %d,\n"
            "  at: [%s], yaw: %s, pitch: %s, dist: %s, fov: %s,\n"
            "};\n" % (props.cam_res_x, props.cam_res_y,
                        ", ".join(num(v) for v in props.cam_at), num(props.cam_yaw),
                        num(props.cam_pitch), num(props.cam_dist), num(props.cam_fov)))


class PC2_OT_export_stage(Operator):
    bl_idname = "pc2.export_stage"
    bl_label = "Export Stage"
    bl_description = ("Write this level's placements, and the camera and frame size "
                      "shared by ALL stages")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.pc2_entity
        coll = props.stage
        if coll is None:
            self.report({'ERROR'}, "Pick a stage first")
            return {'CANCELLED'}
        name = coll.name
        if not IDENT_RE.match(name):
            self.report({'ERROR'}, "'%s' is not a usable export name -- rename the collection"
                        % name)
            return {'CANCELLED'}
        entries, sheared = stage_entries(coll)
        if not entries:
            self.report({'ERROR'}, "%s is empty -- click an entity to place one" % name)
            return {'CANCELLED'}

        path = bpy.path.abspath(props.stages_path)
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(stages_module(existing, {name: stage_block(name, entries)}))
        # The camera object is what the author has been LOOKING through, so it
        # is the truth; the timer polls it, but not within the last tick.
        roll = sync_camera_fields(context)
        with open(game_view_path(props), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(game_view_module(props))

        # The import line names blueprints, so placing something entities.js
        # has not got yet is a stage the build cannot even resolve.
        entities = bpy.path.abspath(props.entities_path)
        missing = sorted({e["entity"] for e in entries} - set(file_block_names(entities)))
        warnings = []
        if roll > 0.05:
            warnings.append("camera roll of %.1f deg dropped -- the game cannot roll" % roll)
        if missing:
            warnings.append("%s not in %s yet -- export %s too"
                            % (", ".join(missing), os.path.basename(entities),
                               "it" if len(missing) == 1 else "them"))
        if sheared:
            warnings.append("%s carry shear a TRS placement cannot represent"
                            % ", ".join(sheared))
        if warnings:
            self.report({'WARNING'}, "%s: %d placement(s) written, but %s"
                        % (name, len(entries), "; and ".join(warnings)))
        else:
            self.report({'INFO'}, "%s: %d placement(s) + shared camera -> %s / game_view.js"
                        % (name, len(entries), os.path.basename(path)))
        return {'FINISHED'}


# Blender keeps no reference to the strings a dynamic enum callback returns,
# so the addon must, or the menu reads freed memory (a documented bpy trap).
_link_items = []


def link_entity_items(_self, context):
    """Every entity but the one being edited, for the Add Linked Entity menu."""
    active = context.scene.pc2_entity.entity
    names = [n for n in entity_names(context) if active is None or n != active.name]
    _link_items[:] = [(n, n, "Place %s inside this entity" % n) for n in names]
    if not _link_items:
        _link_items[:] = [('NONE', "No other entities", "")]
    return _link_items


class PC2_OT_add_entity(Operator):
    bl_idname = "pc2.add_entity"
    bl_label = "Add Linked Entity"
    bl_description = "Place a reusable entity inside this one; edits to the source update every copy"
    bl_options = {'REGISTER', 'UNDO'}

    name: EnumProperty(items=link_entity_items)

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None or self.name == 'NONE':
            return {'CANCELLED'}
        try:
            source = bpy.data.collections.get(self.name)
            if source is None:
                source, _ = build_entity_collection(context, self.name)
            subentities.validate(source, (coll,))
            if not ordered_parts(source):
                raise ValueError("'%s' has no parts" % source.name)
            source.use_fake_user = True
            obj = new_pivot(self.name)
            obj[subentities.MARK] = True
            obj.pc2_source = source
            place_new_part(context, coll, obj)
            subentities.refresh(obj)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class PC2_OT_link_pose(Operator):
    bl_idname = "pc2.link_pose"
    bl_label = "Use Entity Pose"
    bl_description = "Choose this copy's source pose, then save the character state to animate it"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        obj = context.view_layer.objects.active
        if not subentities.is_link(obj):
            return {'CANCELLED'}
        previous = obj.get(subentities.POSE, '')
        obj[subentities.POSE] = self.name
        try:
            subentities.refresh(obj)
        except ValueError as exc:
            obj[subentities.POSE] = previous
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class PC2_OT_add_part(Operator):
    bl_idname = "pc2.add_part"
    bl_label = "Add Part"
    bl_description = "Add this mesh to the entity as a linked instance"
    bl_options = {'REGISTER', 'UNDO'}

    mesh_name: StringProperty()

    def execute(self, context):
        props = context.scene.pc2_entity
        coll = props.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity in the list, or make a new one")
            return {'CANCELLED'}
        source = next((o for o in primitive_objects()
                       if str(o[MESH_PROP]) == self.mesh_name), None)
        if source is None:
            self.report({'ERROR'}, "Unknown primitive '%s' -- reload primitives" % self.mesh_name)
            return {'CANCELLED'}

        # Share the datablock: this is a linked duplicate, which is what the
        # engine instances. Editing this mesh edits every part using it -- and
        # would not reach the game anyway, geometry comes from model.txt.
        obj = bpy.data.objects.new(self.mesh_name, source.data)
        obj[MESH_PROP] = self.mesh_name
        place_new_part(context, coll, obj)
        return {'FINISHED'}


def place_new_part(context, coll, obj):
    """Link a new part into the entity and put it where the panel says.

    Shared by the primitive buttons and Add Pivot, so a pivot lands under the
    active part exactly the way a primitive does -- which is the whole point of
    a pivot, and would be easy to get subtly different in two places.
    """
    # The new part becomes the ACTIVE object below, and Blender must not be in
    # Edit Mode on some other object when that happens: the old object keeps
    # its edit flag with no edit-mesh behind it, "Unable to execute 'Edit Mode',
    # error changing modes" follows, and the edit overlay dereferences null on
    # a later redraw or undo (two crash logs, Sep 2026). Tab into a part, snap
    # the cursor to a vertex, Add Pivot is the workflow that hits it, and it is
    # the RIGHT way to place a hinge -- so leave Edit Mode here rather than
    # refuse. The cursor is scene state and survives the switch.
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    coll.objects.link(obj)
    part_uid(obj)
    parent = context.view_layer.objects.active
    if (context.scene.pc2_entity.parent_to_active
            and parent is not None and parent is not obj
            and is_part(parent) and parent.name in coll.all_objects):
        obj.parent = parent
        # Follow the parent position and rotation, never its scale: parts use
        # scale to SHAPE a primitive (a cube at 1x2.8x1 is a pillar), so
        # inheriting it would turn every attached sphere into an ellipsoid.
        obj.matrix_parent_inverse = scale_free_parent_inverse(parent)
        obj.matrix_basis = Matrix.Identity(4)
    else:
        obj.matrix_basis = Matrix.Translation(context.scene.cursor.location)
    try:
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except RuntimeError:
        pass  # entity collection is not in the view layer; harmless


# --- compound primitives ---------------------------------------------------
#
# One button that stamps SEVERAL parts: a "sphere" that is really two
# hemispheres, base to base. The point is what model.txt then does NOT have to
# ship -- composing beats carrying another sphere-ish mesh every time (deleting
# mesh_capsule for CAPSULE measured -208 zip bytes; retiring mesh_sphere for a
# hemisphere pair, -160). It buys more shapes to build with out of fewer
# meshes, and fewer distinct meshes is fewer draw calls too.
#
# A compound is NOT a new kind of part. It stamps ordinary parts, exports as
# ordinary parts and comes back from entities.js as ordinary parts, so
# entity.js, the exporter and the state system never learn the word -- the
# button is the whole feature.
#
# What holds one together is parenting with the parent's scale INHERITED, so
# scaling the root scales the whole shape and it handles like one primitive.
# That is not a second convention: an identity parent inverse is exactly what
# an IMPORT builds, because it is spawnEntity's `parent.local * local`. The
# scale-FREE parenting the Add buttons use is the special case, and a compound
# opts out of it -- a sphere whose lower half ignores the upper half's scale is
# not a sphere. Inner parts carry RIGID_PROP, so Drop Inherited Scale and
# Mirror leave that parenting alone.
#
# Two rules for the table, both load-bearing:
#   * the FIRST part is the compound's frame. It is placed like any other new
#     part and carries no transform of its own, so the rest are written
#     relative to it -- and it is the part left selected, because scaling the
#     root scales the assembly while scaling both would square it.
#   * every other part is a HALF TURN about the shared origin. A half turn is a
#     sign flip, which commutes with an axis-aligned scale, so a squashed
#     compound stays exact: scale a sphere 1,2,1 and both halves are the same
#     ellipsoid. A quarter turn would shear the moment the root was scaled
#     unevenly, and a TRS part has nowhere to store shear.
COMPOUNDS = (
    {
        "name": "sphere",
        "as": "2 x mesh_hemisphere, base to base. The hemisphere is exactly the top "
              "half of a sphere (y 0..0.510, origin on the equator), so the halves "
              "share an equator ring -- no seam, no gap",
        "parts": (
            {"mesh": "mesh_hemisphere"},
            {"mesh": "mesh_hemisphere", "rot": [180, 0, 0], "parent": 0, "part": "lower"},
        ),
    },
    {
        "name": "cylinder",
        "as": "2 x mesh_hemicylinder, back to back. The hemicylinder is the z >= 0 half of "
              "an 8-sided prism, and its flat wall is the plane the turn mirrors through, "
              "so the two halves share their rim exactly",
        "parts": (
            {"mesh": "mesh_hemicylinder"},
            {"mesh": "mesh_hemicylinder", "rot": [0, 180, 0], "parent": 0, "part": "back"},
        ),
    },
)

# The order the Add buttons read in, two to a row: a HALF on the left and the
# whole it builds on the right, so the pairing is visible in the palette itself.
# Anything not named here follows, alphabetically -- a mesh added to model.txt
# still gets a button without this list having to know about it first.
PALETTE_ORDER = ("cube", "plane",
                 "hemisphere", "sphere",
                 "hemicylinder", "cylinder",
                 "horn", "slice")


def compound_meshes(spec):
    return {p["mesh"] for p in spec["parts"]}


class PC2_OT_add_compound(Operator):
    """A shape the model does not carry, stamped out of the meshes it does."""
    bl_idname = "pc2.add_compound"
    bl_label = "Add Compound"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    @classmethod
    def description(cls, context, properties):
        spec = next((c for c in COMPOUNDS if c["name"] == properties.name), None)
        return ("Add a %s: %s. It is ordinary parts held together by parenting, so it "
                "exports as parts and scales from its root" % (spec["name"], spec["as"])
                if spec else cls.bl_label)

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity in the list, or make a new one")
            return {'CANCELLED'}
        spec = next((c for c in COMPOUNDS if c["name"] == self.name), None)
        if spec is None:
            self.report({'ERROR'}, "Unknown compound '%s'" % self.name)
            return {'CANCELLED'}
        prims = {str(o[MESH_PROP]): o for o in primitive_objects()}
        missing = sorted(compound_meshes(spec) - set(prims))
        if missing:
            self.report({'ERROR'}, "model.txt has no %s -- reload primitives"
                        % ", ".join(missing))
            return {'CANCELLED'}

        created = []
        for i, part in enumerate(spec["parts"]):
            obj = bpy.data.objects.new(
                spec["name"] if i == 0 else "%s.%s" % (spec["name"], part.get("part", i)),
                prims[part["mesh"]].data)
            obj[MESH_PROP] = part["mesh"]
            if i == 0:
                # The frame: placed exactly the way a primitive button places a
                # part, so Parent To Active and the 3D cursor behave as always.
                place_new_part(context, coll, obj)
            else:
                coll.objects.link(obj)
                part_uid(obj)
                obj[RIGID_PROP] = 1
                obj.parent = created[int(part.get("parent", 0))]
                obj.matrix_parent_inverse = Matrix.Identity(4)   # inherit scale
                obj.matrix_basis = conjugate(engine_local(part))
            created.append(obj)
        context.view_layer.update()
        # Selection stays on the frame, which is the handle for the whole shape.
        return {'FINISHED'}


class PC2_OT_add_pivot(Operator):
    """A part that draws nothing, there to be a parent."""
    bl_idname = "pc2.add_pivot"
    bl_label = "Pivot"
    bl_description = ("Add a pivot: an Empty that draws nothing and exports as a part with no "
                      "mesh. Parent parts to it and they turn and scale about IT rather than "
                      "about their own centres -- a jaw hinge, a shoulder")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity in the list, or make a new one")
            return {'CANCELLED'}
        place_new_part(context, coll, new_pivot(PIVOT_NAME))
        return {'FINISHED'}


class PC2_OT_drop_inherited_scale(Operator):
    bl_idname = "pc2.drop_inherited_scale"
    bl_label = "Drop Inherited Scale"
    bl_description = ("Make the selected parts follow their parent's position and rotation "
                      "but not its scale, without moving them. Re-run after rescaling a parent")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Parents first: fixing one changes where its children sit.
        # A compound's inner parts are left alone -- following the parent's
        # scale is what makes the assembly one shape, and dropping it would
        # leave a scaled sphere as a hemisphere with a smaller one inside it.
        selected = [o for o in context.selected_objects
                    if o.parent is not None and is_part(o)]
        rigid = [o for o in selected if o.get(RIGID_PROP)]
        targets = sorted((o for o in selected if not o.get(RIGID_PROP)),
                         key=hierarchy_depth)
        if not targets:
            self.report({'ERROR'}, "Compound parts keep their parent's scale by design"
                        if rigid else "Select parented parts first")
            return {'CANCELLED'}
        for obj in targets:
            parent = obj.parent
            frame = loc_rot_only(parent.matrix_world)
            # Keep where the part is and how it is turned; keep its OWN size.
            loc, quat, _ = obj.matrix_world.decompose()
            _, _, own_scale = obj.matrix_basis.decompose()
            obj.matrix_parent_inverse = scale_free_parent_inverse(parent)
            obj.matrix_basis = frame.inverted() @ Matrix.LocRotScale(loc, quat, own_scale)
            context.view_layer.update()
        self.report({'INFO'}, "Dropped inherited scale on %d part(s)%s"
                    % (len(targets),
                       ", left %d compound part(s) alone" % len(rigid) if rigid else ""))
        return {'FINISHED'}


class PC2_OT_flat_preview(Operator):
    """Make the viewport show what the engine draws: flat, unlit colour.

    The materials are only half of it. Every material in play is an Emission
    node (the importer's for textures, color_material's for overrides), which
    is perfectly flat -- but SOLID shading ignores node graphs entirely and
    lights `diffuse_color` with a studio lamp, which is the gradient across a
    curved part. That is a per-VIEWPORT setting, so no material can fix it.

    The view transform is the other trap: under AgX a flat #fe0000 is not
    #fe0000 on screen, so a swatch and the part wearing it disagree for a
    reason that has nothing to do with shading.
    """
    bl_idname = "pc2.flat_preview"
    bl_label = "Flat Preview"
    bl_description = ("Set this viewport to show parts flat and unlit, the way the engine "
                      "draws them: Material Preview, Solid shading set to flat lighting, "
                      "and the Standard view transform so a palette colour appears as "
                      "itself rather than through AgX")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        views = [space for area in context.screen.areas if area.type == 'VIEW_3D'
                 for space in area.spaces if space.type == 'VIEW_3D']
        for space in views:
            space.shading.type = 'MATERIAL'    # renders the Emission graphs
            # ... and leave Solid flat too, for when it is switched back.
            space.shading.light = 'FLAT'
            space.shading.color_type = 'TEXTURE'
        view = context.scene.view_settings
        try:
            view.view_transform = 'Standard'
            view.look = 'None'
        except TypeError:
            pass          # a Blender whose enum spells these differently
        view.exposure = 0.0
        view.gamma = 1.0
        self.report({'INFO'}, "Flat preview: %d viewport(s), Standard view transform"
                    % len(views))
        return {'FINISHED'}


class PC2_OT_mirror_parts(Operator):
    """Copy a sub-assembly to the other side.

    Blender's own mirroring reaches for a negative scale, which this format
    would rather not carry: scale is how a part is SHAPED, and a negative one
    is a reflection the engine has to draw inside-out. Conjugating by the
    reflection instead (see mirror_x) keeps the scale positive and leaves the
    reflection on the primitive, which is symmetric enough not to show it.
    """
    bl_idname = "pc2.mirror_parts"
    bl_label = "Mirror Across X"
    bl_description = ("Duplicate the selected parts and everything parented under them, "
                      "mirrored across x = 0 in their parent's space. Scale stays positive")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity in the list, or make a new one")
            return {'CANCELLED'}
        members = set(o for o in coll.all_objects if is_part(o))
        chosen = [o for o in context.selected_objects if o in members]
        if not chosen:
            self.report({'ERROR'}, "Select the part(s) to mirror")
            return {'CANCELLED'}

        targets = set()
        for obj in chosen:
            targets.add(obj)
            targets.update(part_descendants(obj, members))
        # Parents first: a copy's transform is set relative to its parent, so
        # the parent has to exist and be placed before the child asks.
        order = sorted(targets, key=hierarchy_depth)

        copies = {}
        for obj in order:
            # obj.copy() shares the mesh datablock (a linked duplicate, which
            # is what the engine instances) and brings the object-linked
            # material slot along, so the colour or tile preview copies too.
            copies[obj] = obj.copy()
            copies[obj][UID_PROP] = uuid.uuid4().hex
            for target in obj.users_collection:
                target.objects.link(copies[obj])

        for obj in order:
            dup = copies[obj]
            if obj.parent in copies:
                dup.parent = copies[obj.parent]
                context.view_layer.update()   # the new parent has moved
                # matrix_local below lands the copy correctly whichever inverse
                # this is, so what is copied here is how the part behaves
                # LATER: a compound's inner part has to keep following its
                # parent's scale, or the mirrored sphere comes apart the first
                # time it is resized.
                dup.matrix_parent_inverse = (
                    Matrix.Identity(4) if obj.get(RIGID_PROP)
                    else scale_free_parent_inverse(dup.parent))
            # A part outside the selection keeps its original parent, so the
            # mirror plane is that parent's x = 0 -- a limb mirrors with the
            # torso it hangs off, not around the entity origin.
            dup.matrix_local = mirror_x(obj.matrix_local)
            if subentities.is_link(dup):
                dup[subentities.MIRROR] = not bool(obj.get(subentities.MIRROR))
                subentities.refresh(dup)
        context.view_layer.update()

        try:
            for other in context.selected_objects:
                other.select_set(False)
            for dup in copies.values():
                dup.select_set(True)
            context.view_layer.objects.active = copies[order[0]]
        except RuntimeError:
            pass  # entity collection is not in the view layer; harmless

        textured = sorted(set(o.name for o in order if uv_spec_of(o) is not None))
        if textured:
            self.report({'WARNING'},
                        "Mirrored %d part(s); the texture is mirrored too on %s"
                        % (len(order), ", ".join(textured)))
        else:
            self.report({'INFO'}, "Mirrored %d part(s)" % len(order))
        return {'FINISHED'}


class PC2_OT_set_color(Operator):
    bl_idname = "pc2.set_color"
    bl_label = "Set Colour"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    @classmethod
    def description(cls, context, properties):
        # Per-button tooltips: the swatches are colours with no text on them,
        # so the index has to be readable somewhere.
        if properties.index < 0:
            return "Forget the colour and go back to the texture underneath"
        return ("Draw this flat in palette colour %d. Any UV tile steps "
                "aside -- the engine gives a colour-overridden instance no "
                "texture lookup at all -- but it stays remembered"
                % properties.index)

    def execute(self, context):
        # A part and a whole PLACEMENT take the same override: drawEntity hands
        # the engine one colour either way, so this is one operator, not two.
        wanted = lambda o: mesh_name_of(o) is not None or is_placement(o)
        parts = [o for o in context.selected_objects if wanted(o)]
        active = context.view_layer.objects.active
        if not parts and active is not None and wanted(active):
            parts = [active]
        if not parts:
            self.report({'ERROR'}, "Select a part or a placement first")
            return {'CANCELLED'}
        for obj in parts:
            if is_placement(obj):
                if self.index < 0:
                    obj.pop(COLOR_PROP, None)
                else:
                    obj[COLOR_PROP] = self.index
                sync_instance_color(obj)
                continue
            if self.index < 0:
                # Removed, not set to -1: color_of() then falls back to a
                # colour material assigned by hand, which is the documented
                # way to give a part a colour without this panel.
                obj.pop(COLOR_PROP, None)
                if obj.pc2.draws == 'COLOR':
                    obj.pc2.draws = 'TEXTURE'
            else:
                obj[COLOR_PROP] = self.index
                obj.pc2.draws = 'COLOR'
            refresh_part_preview(obj)
        return {'FINISHED'}


class PC2_OT_copy_palette(Operator):
    """Whatever the viewport is previewing, as the string main.js wants.

    The dropdown is a preview and stays one -- a part stores a palette INDEX,
    so nothing about a blueprint changes when the colours do. This is the one
    step that was missing: liking a palette on screen and then having to
    transcribe 16 Lospec colours by hand into 96 hex characters.
    """
    bl_idname = "pc2.copy_palette"
    bl_label = "Copy as a PALETTES string"
    bl_description = ("Put the 16 colours on screen on the clipboard as the 96 hex "
                      "chars main.js's PALETTES and PALETTE_BANKS are written in")

    def execute(self, context):
        colors = scene_palette(context)
        if len(colors) != 16:
            # model_palette returns () when model.txt cannot be read, and a
            # 0-colour "palette" would serialise to an empty string that looks
            # like a valid "keep the model's own" entry.
            self.report({'ERROR'}, "No 16-colour palette on screen to copy")
            return {'CANCELLED'}
        text = palette_hex(colors)
        context.window_manager.clipboard = text
        print("pc2 palette: '%s'" % text)      # so a headless run can read it too
        self.report({'INFO'}, "96 hex chars copied -- paste into PALETTES or PALETTE_BANKS")
        return {'FINISHED'}


class PC2_OT_set_bank(Operator):
    """Which palette bank a PLACEMENT resolves its colours through.

    Placements only: a bank is per-instance, so it is the one thing that lets
    two copies of one blueprint wear different colours in the same frame. A
    part cannot have one -- it would be asking for two palettes inside a single
    entity, which the engine draws in one instanced call.
    """
    bl_idname = "pc2.set_bank"
    bl_label = "Set Palette Bank"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    def execute(self, context):
        placements = [o for o in context.selected_objects if is_placement(o)]
        active = context.view_layer.objects.active
        if not placements and is_placement(active):
            placements = [active]
        if not placements:
            self.report({'ERROR'}, "Select a placement first")
            return {'CANCELLED'}
        for obj in placements:
            # Removed rather than set to 0, so a stage using no banks exports
            # the same bytes it did before banks existed.
            if self.index:
                obj[BANK_PROP] = self.index
            else:
                obj.pop(BANK_PROP, None)
        return {'FINISHED'}


class PC2_OT_pick_tile(Operator):
    bl_idname = "pc2.pick_tile"
    bl_label = "Pick Tile"
    bl_options = {'REGISTER', 'UNDO'}

    u: IntProperty(default=1)
    v: IntProperty(default=1)

    @classmethod
    def description(cls, context, properties):
        return ("Draw this part from atlas tile %d, %d. Any colour override "
                "steps aside -- it stays remembered" % (properties.u, properties.v))

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or not mesh_name_of(obj):
            self.report({'ERROR'}, "Select a part first")
            return {'CANCELLED'}
        obj.pop(UV_PROP, None)   # a verbatim spec would win over the fields
        with quiet_uv(obj) as p:
            p.uv_tile_u, p.uv_tile_v = self.u, self.v
            p.draws = 'TILE'
        return {'FINISHED'}


class PC2_OT_clear_uv(Operator):
    bl_idname = "pc2.clear_uv"
    bl_label = "Clear UV"
    bl_description = "Drop this part's UV retile and go back to the mesh's own atlas patch"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or not mesh_name_of(obj):
            self.report({'ERROR'}, "Select a part first")
            return {'CANCELLED'}
        obj.pop(UV_PROP, None)
        with quiet_uv(obj) as p:
            if p.draws == 'TILE':
                p.draws = 'TEXTURE'
            p.uv_repeat_u = 1.0
            p.uv_repeat_v = 1.0
        return {'FINISHED'}


class PC2_OT_new_state(Operator):
    bl_idname = "pc2.new_state"
    bl_label = "New State"
    bl_description = ("Capture the current arrangement as a new named state. Made from the "
                      "base pose, it also refreshes the entity's recorded base")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", default="STATE")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity first")
            return {'CANCELLED'}
        name = re.sub(r"[\r\n]+", " ", self.name).strip()
        if not name:
            self.report({'ERROR'}, "State name cannot be empty")
            return {'CANCELLED'}
        try:
            data = state_data(coll)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if any(saved.get("name") == name for saved in data["states"]):
            self.report({'ERROR'}, "State '%s' already exists" % name)
            return {'CANCELLED'}
        current = entity_snapshot(coll)
        # Leaving the base pose is the moment the base is knowable: the scene
        # IS the base until a state is applied over it. Made from another
        # state's pose, the new state inherits that state's posed parts.
        if not data["active"] or not data["states"]:
            data["base"] = current
        data["states"].append({"name": name, "parts": mark_posed(
            {uid: dict(item) for uid, item in current.items()}, data["base"])})
        data["active"] = name
        save_state_data(coll, data)
        self.report({'INFO'}, "Editing state '%s' -- pose it, then Save Active State" % name)
        return {'FINISHED'}


class PC2_OT_save_state(Operator):
    bl_idname = "pc2.save_state"
    bl_label = "Save Active State"
    bl_description = "Update the active state from the arrangement and visibility in the viewport"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            return {'CANCELLED'}
        try:
            data = state_data(coll)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        active = data["active"]
        saved = next((s for s in data["states"] if s.get("name") == active), None)
        if saved is None:
            self.report({'ERROR'}, "Apply or create a state before saving")
            return {'CANCELLED'}
        saved["parts"] = mark_posed(entity_snapshot(coll), data["base"])
        save_state_data(coll, data)
        self.report({'INFO'}, "Saved state '%s'" % active)
        return {'FINISHED'}


class PC2_OT_apply_state(Operator):
    bl_idname = "pc2.apply_state"
    bl_label = "Apply State"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            return {'CANCELLED'}
        try:
            data = state_data(coll)
            saved = next((s for s in data["states"] if s.get("name") == self.name), None)
            if saved is None:
                raise ValueError("State '%s' no longer exists" % self.name)
            # The scene is the base until now; record it so the unposed parts
            # of every state follow whatever was just edited.
            if not data["active"]:
                data["base"] = entity_snapshot(coll)
            apply_snapshot(context, coll, resolve_state(coll, data, saved))
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        data["active"] = self.name
        save_state_data(coll, data)
        self.report({'INFO'}, "Applied state '%s'" % self.name)
        return {'FINISHED'}


class PC2_OT_apply_base(Operator):
    bl_idname = "pc2.apply_base"
    bl_label = "Apply Base Pose"
    bl_description = "Restore the blueprint arrangement; entity export is allowed only here"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            return {'CANCELLED'}
        try:
            data = state_data(coll)
            if not data["base"]:
                raise ValueError("No base pose yet -- New State records it automatically")
            apply_snapshot(context, coll, data["base"])
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        data["active"] = ""
        save_state_data(coll, data)
        self.report({'INFO'}, "Applied base pose")
        return {'FINISHED'}


class PC2_OT_sync_states(Operator):
    bl_idname = "pc2.sync_states"
    bl_label = "Sync States to Hierarchy"
    bl_description = ("Re-express every saved state under the current parenting, keeping each "
                      "part's world pose. Also gives states a part they predate, and drops "
                      "parts that no longer exist. Run this from the base pose after "
                      "re-parenting, adding or deleting a part")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            self.report({'ERROR'}, "Pick an entity first")
            return {'CANCELLED'}
        try:
            data = state_data(coll)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not data["states"]:
            self.report({'ERROR'}, "This entity has no states")
            return {'CANCELLED'}
        if states_unstamped(data):
            self.report({'ERROR'}, "These states predate parent tracking -- save and reopen this "
                                   ".blend to record the tree they were captured in, then sync")
            return {'CANCELLED'}
        # Parenting is base-pose authoring, the same discipline export follows.
        # Refusing here is not fussiness: the live scene is the only truthful
        # record of the new base, and it only says the base while the base is
        # what is applied.
        if data["active"]:
            self.report({'ERROR'}, "Apply Base Pose first -- the live scene is what the new "
                                   "base is read from")
            return {'CANCELLED'}
        objects = ordered_parts(coll)
        moved, added, removed = states_drift(data, objects)
        if not (moved or added or removed):
            self.report({'INFO'}, "States already match the hierarchy")
            return {'FINISHED'}
        fresh = entity_snapshot(coll)
        try:
            for saved in data["states"]:
                saved["parts"] = rebase_snapshot(saved.get("parts") or {}, objects, fresh)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        data["base"] = fresh
        save_state_data(coll, data)
        self.report({'INFO'}, "Synced %d state(s): %d re-parented, %d added, %d removed"
                    % (len(data["states"]), len(moved), len(added), len(removed)))
        return {'FINISHED'}


class PC2_OT_delete_state(Operator):
    bl_idname = "pc2.delete_state"
    bl_label = "Delete State"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll is None:
            return {'CANCELLED'}
        try:
            data = state_data(coll)
            before = len(data["states"])
            if data["active"] == self.name and data["base"]:
                apply_snapshot(context, coll, data["base"])
                data["active"] = ""
            data["states"] = [s for s in data["states"] if s.get("name") != self.name]
            if len(data["states"]) == before:
                raise ValueError("State '%s' no longer exists" % self.name)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        save_state_data(coll, data)
        self.report({'INFO'}, "Deleted state '%s'" % self.name)
        return {'FINISHED'}


class PC2_OT_export_entity(Operator):
    bl_idname = "pc2.export_entity"
    bl_label = "Export Entity"
    bl_description = "Write this entity's blueprint into src/entities.js"
    bl_options = {'REGISTER'}

    def execute(self, context):
        coll = context.scene.pc2_entity.entity
        if coll and any(subentities.is_link(o) for o in coll.all_objects):
            try:
                with subentities.expanded_collection(context, coll) as expanded:
                    return self.export_collection(context, expanded, coll.name)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
        return self.export_collection(context, coll)

    def export_collection(self, context, coll, export_name=None):
        props = context.scene.pc2_entity
        if coll is None:
            self.report({'ERROR'}, "No entity collection selected")
            return {'CANCELLED'}
        name = export_name or coll.name
        if not IDENT_RE.match(name):
            self.report({'ERROR'}, "'%s' is not a valid entity name" % name)
            return {'CANCELLED'}

        objs = ordered_parts(coll)
        if not objs:
            self.report({'ERROR'}, "'%s' has no parts" % name)
            return {'CANCELLED'}
        try:
            states_data = state_data(coll)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if states_data["active"]:
            self.report({'ERROR'}, "Apply Base Pose before exporting (currently '%s')"
                        % states_data["active"])
            return {'CANCELLED'}

        known = model_mesh_names(bpy.path.abspath(props.model_path))
        if known:
            unknown = sorted(set(mesh_name_of(o) for o in objs) - known - {None})
            if unknown:
                self.report({'ERROR'}, "Not in model.txt: %s" % ", ".join(unknown))
                return {'CANCELLED'}

        # ordered_parts is the one DFS used by both the blueprint and states,
        # so UID-keyed snapshots are remapped to exactly these runtime slots.
        order = objs
        index = {obj: i for i, obj in enumerate(order)}

        sheared = []
        flattened = []
        flat_uids = set()
        parts = []
        labels = []
        for obj in order:
            pos, rot, scale, exact = decompose_part(obj)
            parent_index = index.get(obj.parent)
            if not exact:
                # Plain Ctrl+P hides a parent's non-uniform scale behind a
                # non-identity parent inverse: the part looks unskewed in
                # Blender while its LOCAL matrix carries shear no TRS part can
                # hold, so the engine would draw it skewed instead.
                # spawnEntity bakes parent.local * local ONCE at spawn, so the
                # `parent` field is pure authoring convenience -- re-emitting
                # this part as a root with its world transform is identical at
                # runtime and matches what Blender draws.
                w_pos, w_rot, w_scale, w_exact = decompose_part(obj, world=True)
                if w_exact:
                    pos, rot, scale = w_pos, w_rot, w_scale
                    parent_index = None
                    flattened.append(obj.name)
                    flat_uids.add(part_uid(obj))
                else:
                    sheared.append(obj.name)
            # No `mesh` key at all is what makes it a pivot -- there is no
            # type field to disagree with itself.
            part = {} if is_pivot(obj) else {"mesh": mesh_name_of(obj)}
            emit_trs(part, pos, rot, scale)
            color = active_color(obj)
            if color is not None:
                part["color"] = color
            uv = uv_spec_of(obj)
            if uv:
                part["uv"] = uv
            if parent_index is not None:
                part["parent"] = parent_index
            parts.append(part)
            labels.append(label_of(obj))
        for i, obj in enumerate(order):
            obj[ORDER_PROP] = i

        # Flattening is safe for states too -- snapshot_matrices composes their
        # world transform the same way this loop just did for the base. Real
        # shear is not: the blueprint part is already an approximation of a
        # matrix TRS cannot hold, so a state offset from it means nothing.
        if states_data["states"] and sheared:
            self.report({'ERROR'}, "Stateful entities cannot shear %s -- its WORLD matrix is "
                        "skewed, so no pos/rot/scale state can describe it"
                        % ", ".join(sheared))
            return {'CANCELLED'}
        state_js = None
        if states_data["states"]:
            # A re-parented part is the silent case: its UID is still there, so
            # nothing is missing -- only the frame its stored LOCAL means
            # anything in has moved. Refuse rather than write a state that
            # would put the part somewhere it was never posed.
            if states_unstamped(states_data):
                self.report({'ERROR'}, "These states predate parent tracking -- save and reopen "
                            "this .blend so a re-parent can still be detected")
                return {'CANCELLED'}
            moved, added, removed = states_drift(states_data, order)
            if moved or added or removed:
                self.report({'ERROR'}, "States predate the hierarchy (%d re-parented, %d added, "
                            "%d removed) -- run Sync States to Hierarchy"
                            % (len(moved), len(added), len(removed)))
                return {'CANCELLED'}
            # The live base may have been edited since the first state was
            # captured. States are full snapshots, so rebase their compact
            # overrides against what this export is writing now.
            states_data["base"] = entity_snapshot(coll)
            try:
                state_js = serialize_runtime_states(coll, name, states_data, order, parts,
                                                    flat_uids)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            save_state_data(coll, states_data)

        block = "\n".join(["export const %s = [" % name]
                          + [serialize_part(p, label) for p, label in zip(parts, labels)]
                          + ["];"])

        path = bpy.path.abspath(props.entities_path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        else:
            text = DEFAULT_HEADER
        # An export always replaces: you can only be editing an entity you
        # picked out of this list, or one whose name New Entity checked against
        # the file. Dump whatever is being replaced to the system console, so
        # the previous version is always recoverable by hand.
        previous = entity_block(text, name)
        previous_states = state_block(text, name)
        if previous is not None:
            print("[pc2] replacing:\n%s%s"
                  % (previous, "\n" + previous_states if previous_states else ""))

        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(upsert_states(upsert_entity(text, name, block), name, state_js))

        warnings = []
        if sheared:
            warnings.append("%s carry shear no TRS part can represent, so the engine will "
                            "draw them differently from Blender" % ", ".join(sheared))
        if previous is not None and "uv:" in previous and "uv:" not in block:
            warnings.append("the previous version had uv retiling and this one does not -- "
                            "see the system console if that was not intended")

        # Flattening is the NORMAL outcome of scale-free parenting, not a
        # problem, so it is reported but never as a warning.
        note = " (%d baked to world space)" % len(flattened) if flattened else ""
        if warnings:
            self.report({'WARNING'}, "%s: %d part(s) written%s, but %s"
                        % (name, len(parts), note, "; and ".join(warnings)))
        else:
            self.report({'INFO'}, "%s: %d part(s)%s -> %s"
                        % (name, len(parts), note, os.path.basename(path)))
        return {'FINISHED'}


# --- panel -----------------------------------------------------------------

class PC2_PT_view(Panel):
    """How the VIEWPORT draws -- which is neither an entity's business nor a
    stage's, and is why this sits above both rather than inside either.

    Game Preview, Flat Preview and the palette were under Entity, where a stage
    could not reach them; duplicating them into Stage would have been two
    copies of the same three controls, free to drift. They are scene-level
    properties and always were, so the only thing that was ever entity-shaped
    about them was where they were drawn.
    """
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "View"

    def draw(self, context):
        layout = self.layout
        props = context.scene.pc2_entity

        col = layout.column(align=True)
        col.prop(props, "palette", text="")
        if props.palette == PALETTE_STAGE:
            # Say which stage, and whether it actually swaps anything: a stage
            # with no PALETTES entry keeps the model's own, and a silent
            # fallback would read as the option being broken.
            stage = props.stage
            mood = game_palettes(main_js_path(props)).get(stage.name) if stage else None
            if stage is None:
                col.label(text="no stage picked", icon='INFO')
            elif mood:
                col.label(text="%s swaps its own 16 colours" % stage.name, icon='CHECKMARK')
            else:
                col.label(text="%s keeps the model's palette" % stage.name, icon='INFO')
        elif bank_choice(props.palette):
            # Say whether main.js actually loads this bank. A bank it does not
            # define draws in the model's own colours, and a silent fallback
            # would read as the option being broken -- the same reasoning as
            # the stage-mood branch above.
            n = bank_choice(props.palette)
            banks = game_bank_palettes(main_js_path(props))
            if n > len(banks):
                col.label(text="main.js loads no bank %d" % n, icon='INFO')
            elif banks[n - 1] is None:
                col.label(text="bank %d keeps the model's palette" % n, icon='INFO')
            else:
                col.label(text="every placement marked b: %d" % n, icon='CHECKMARK')
            # The viewport paints ONE palette, and collection instances share
            # their materials, so a per-placement preview is not available at
            # any price -- better to say so than to let the viewport quietly
            # disagree with the game.
            col.label(text="whole scene, not just b: %d" % n, icon='INFO')
        elif props.palette != PALETTE_MODEL:
            col.label(text="preview only, model.txt unchanged", icon='INFO')

        # The 16 colours themselves, read-only: labels, not the Part panel's
        # operator buttons, so looking at a mood from here cannot recolour the
        # part that happens to be selected.
        icons = palette_icons(scene_palette(context), -1, False)
        if icons is not None:
            grid = layout.grid_flow(row_major=True, columns=8, even_columns=True, align=True)
            for i in range(16):
                grid.label(text="", icon_value=icons["pal%d" % i].icon_id)

        # Getting a previewed palette INTO the game. The swatches are already
        # in slot order, so this is a straight serialisation -- but the slots
        # carry roles the colours know nothing about, hence the warning.
        col = layout.column(align=True)
        col.operator(PC2_OT_copy_palette.bl_idname, icon='COPYDOWN')
        col.label(text="slot 0 = background, 6 = crosshair, 10 = HUD", icon='INFO')

        col = layout.column()
        col.prop(props, "game_preview", toggle=True, icon='SHADING_RENDERED')
        if props.game_preview:
            col.prop(props, "game_pixel_scale")
        else:
            col.operator(PC2_OT_flat_preview.bl_idname, icon='SHADING_TEXTURE')


class PC2_PT_entity(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "Entity"

    def draw(self, context):
        layout = self.layout
        props = context.scene.pc2_entity
        active = props.entity
        prims = primitive_objects()
        if not prims:
            autoload_primitives()

        box = layout.box()
        box.label(text="Entities", icon='OUTLINER_COLLECTION')
        names = entity_names(context)
        if not names:
            box.label(text="None yet", icon='INFO')
        else:
            col = box.column(align=True)
            for name in names:
                # The icon says whether clicking edits what is already in this
                # .blend or reads the blueprint out of entities.js first.
                here = bpy.data.collections.get(name) is not None
                col.operator(
                    PC2_OT_edit_entity.bl_idname, text=name,
                    icon='OUTLINER_COLLECTION' if here else 'IMPORT',
                    depress=(active is not None and active.name == name),
                ).name = name
        box.operator(PC2_OT_new_entity.bl_idname, icon='ADD')

        box = layout.box()
        if active is None:
            box.label(text="Pick an entity above", icon='INFO')
        elif not prims:
            box.label(text=_autoload["error"] or "Loading primitives...",
                      icon='ERROR' if _autoload["error"] else 'IMPORT')
        else:
            parts = [o for o in active.all_objects if is_part(o)]
            box.label(text="Add to %s (%d part%s)"
                      % (active.name, len(parts), "" if len(parts) == 1 else "s"),
                      icon='MESH_DATA')
        col = box.column()
        col.enabled = active is not None and bool(prims)
        grid = col.grid_flow(row_major=True, columns=2, even_columns=True)
        # Meshes and compounds go into ONE list before anything is drawn: from
        # the palette a compound IS a primitive, and PALETTE_ORDER pairs each
        # half with its whole, which only works if they can interleave.
        have = {str(o[MESH_PROP]) for o in prims}
        buttons = [(str(o[MESH_PROP])[5:] if str(o[MESH_PROP]).startswith("mesh_")
                    else str(o[MESH_PROP]), str(o[MESH_PROP]), None) for o in prims]
        buttons += [(spec["name"], None, spec["name"]) for spec in COMPOUNDS
                    if compound_meshes(spec) <= have]   # only what this model can build
        buttons.sort(key=lambda b: (PALETTE_ORDER.index(b[0])
                                    if b[0] in PALETTE_ORDER else len(PALETTE_ORDER), b[0]))
        for label, mesh, compound in buttons:
            if mesh is not None:
                grid.operator(PC2_OT_add_part.bl_idname, text=label).mesh_name = mesh
            else:
                grid.operator(PC2_OT_add_compound.bl_idname, text=label).name = compound
        col.operator(PC2_OT_add_pivot.bl_idname, icon='EMPTY_AXIS')
        # One dropdown rather than a button per entity: the list grows with
        # every entity authored and was crowding the palette out of the panel.
        col.operator_menu_enum(PC2_OT_add_entity.bl_idname, "name",
                               text="Add Linked Entity", icon='LINKED')
        col.prop(props, "parent_to_active")
        col.operator(PC2_OT_drop_inherited_scale.bl_idname, icon='CON_SIZELIKE')
        col.operator(PC2_OT_mirror_parts.bl_idname, icon='MOD_MIRROR')
        # Game Preview / Flat Preview / the palette used to live here. They are
        # in the View panel above now: none of them is about the entity being
        # edited -- they are how the VIEWPORT draws, and a stage wants them just
        # as much. One copy, or the two drift.

        col = layout.column()
        col.enabled = active is not None
        col.operator(PC2_OT_export_entity.bl_idname, icon='EXPORT',
                     text=("Export %s" % active.name) if active else "Export Entity")


class PC2_PT_part(Panel):
    """Everything that is per-PART rather than per-entity: the colour override
    and the UV retile, the two things `draw()` takes besides a matrix."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "Part"
    bl_parent_id = "PC2_PT_entity"

    def draw(self, context):
        layout = self.layout
        obj = context.view_layer.objects.active
        # No poll: a panel that disappears when nothing is selected is a panel
        # you cannot find in the first place.
        if subentities.is_link(obj):
            layout.prop(obj, "name", text="")
            source = obj.pc2_source
            if source is None:
                layout.label(text="Source entity is missing", icon='ERROR')
                return
            layout.operator(PC2_OT_edit_entity.bl_idname,
                            text="Edit %s" % source.name, icon='LINKED').name = source.name
            layout.label(text="Pose for this copy")
            current = obj.get(subentities.POSE, '')
            layout.operator(PC2_OT_link_pose.bl_idname, text="Base", depress=not current).name = ''
            for saved in state_data(source)['states']:
                name = saved['name']
                layout.operator(PC2_OT_link_pose.bl_idname, text=name,
                                depress=current == name).name = name
            layout.label(text="Save a character state to animate this pose")
            if obj.get('pc2_link_error'):
                layout.label(text=obj['pc2_link_error'], icon='ERROR')
            if obj.get('pc2_link_note'):
                layout.label(text=obj['pc2_link_note'], icon='INFO')
            return
        if obj is None or mesh_name_of(obj) is None:
            layout.label(text="Select a part to colour or retile it", icon='INFO')
            return
        # The name IS the label: it is exported as the part's trailing comment,
        # so this field is how a part stops being "the third mesh_slice".
        row = layout.row(align=True)
        row.label(text="", icon='TEXTURE')
        row.prop(obj, "name", text="")
        # The three ways a part can draw are mutually exclusive in the engine,
        # so this says which is IN EFFECT rather than making you tick one box
        # and untick another. Picking a swatch or a tile below moves it too.
        layout.prop(obj.pc2, "draws", expand=True)
        self.draw_color(context, obj, layout.box())
        self.draw_uv(context, obj, layout.box())

    def draw_color(self, context, obj, box):
        # Which 16 colours these swatches are drawn in is the View panel's
        # dropdown, not a per-part choice -- a part stores an INDEX either way.
        box.label(text="Colour", icon='COLOR')
        draw_swatches(context, box, part_color(obj), obj.pc2.draws == 'COLOR')

    def draw_uv(self, context, obj, box):
        box.label(text="UV", icon='UV')
        raw = obj.get(UV_PROP)
        if raw:
            # A shape neither editor authors (a rect). Show it, do not fake
            # controls for it, and let Clear UV drop it.
            box.label(text="Custom uv, carried verbatim:", icon='INFO')
            box.label(text=str(raw))
            box.operator(PC2_OT_clear_uv.bl_idname, icon='X')
            return

        p = obj.pc2
        size = tile_size(p)
        row = box.row(align=True)
        row.prop(p, "uv_tile_size", expand=True)
        _, image = base_texture_image(obj)
        marker = (tile_rect(p.uv_tile_u, p.uv_tile_v, size, image.size[1])
                  if image is not None else None)
        tiles = (atlas_cells(image, marker, p.draws == 'TILE')
                 if image is not None else None)
        if tiles is not None:
            # A column of aligned rows, narrowed until the cells hug their
            # icons: a button is about 1.4 icons wide by default, which reads
            # as a grid of chips rather than as the atlas.
            centre = box.row()
            centre.alignment = 'CENTER'
            col = centre.column(align=True)
            col.scale_x = TILE_CELL
            col.scale_y = TILE_CELL
            for v in range(1, tiles["rows"] + 1):
                row = col.row(align=True)
                for u in range(1, tiles["cols"] + 1):
                    op = row.operator(
                        PC2_OT_pick_tile.bl_idname, text="", emboss=False,
                        icon_value=tiles["pcoll"]["t%d_%d" % (u, v)].icon_id)
                    # A cell is a fixed 16 texels: click through to whichever
                    # tile covers it. At size 8 that reaches every other tile,
                    # which is what the U/V fields below are for.
                    op.u = (u - 1) * CELL // size + 1
                    op.v = (v - 1) * CELL // size + 1
        row = box.row(align=True)
        row.prop(p, "uv_tile_u")
        row.prop(p, "uv_tile_v")

        if p.draws == 'TILE':
            filled = tile_occupancy(obj)
            if filled is not None and filled <= 0.0:
                warn = box.box()
                warn.label(text="This tile is empty", icon='ERROR')
                warn.label(text="the engine discards it -- part draws nothing")
            elif filled is not None:
                box.label(text="tile %d, %d is %d%% drawn"
                          % (p.uv_tile_u, p.uv_tile_v, round(filled * 100)),
                          icon='CHECKMARK')

        col = box.column(align=True)
        col.prop(p, "uv_repeat_u")
        col.prop(p, "uv_repeat_v")

        spec = uv_spec_of(obj)
        box.label(text="uv: %s" % spec if spec else "uv: (none)")
        box.operator(PC2_OT_clear_uv.bl_idname, icon='X')


class PC2_PT_states(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "States"
    bl_parent_id = "PC2_PT_entity"

    def draw(self, context):
        layout = self.layout
        coll = context.scene.pc2_entity.entity
        if coll is None:
            layout.label(text="Pick an entity first", icon='INFO')
            return
        try:
            data = state_data(coll)
        except ValueError as exc:
            layout.label(text=str(exc), icon='ERROR')
            return

        active = data["active"]
        layout.label(text="Editing: %s" % (active or "Base Pose"),
                     icon='POSE_HLT' if active else 'OUTLINER_DATA_EMPTY')
        row = layout.row(align=True)
        row.operator(PC2_OT_new_state.bl_idname, icon='ADD')
        save = row.row(align=True)
        save.enabled = bool(active)
        save.operator(PC2_OT_save_state.bl_idname, icon='FILE_TICK')

        base = layout.row()
        base.enabled = bool(data["base"])
        base.operator(PC2_OT_apply_base.bl_idname, icon='LOOP_BACK',
                      depress=not active)

        for saved in data["states"]:
            name = str(saved.get("name") or "STATE")
            row = layout.row(align=True)
            row.operator(PC2_OT_apply_state.bl_idname, text=name,
                         icon='POSE_HLT', depress=(name == active)).name = name
            row.operator(PC2_OT_delete_state.bl_idname, text="", icon='X').name = name

        # A stored pose is a LOCAL matrix, so re-parenting reinterprets it
        # rather than losing it -- which is invisible until a state is applied
        # and a limb jumps. Say so where the states are, and offer the fix.
        if data["states"] and states_unstamped(data):
            box = layout.box()
            box.label(text="States predate parent tracking", icon='ERROR')
            box.label(text="Save and reopen this .blend to record their tree")
        elif data["states"]:
            moved, added, removed = states_drift(data, ordered_parts(coll))
            if moved or added or removed:
                box = layout.box()
                box.label(text="Hierarchy moved since these states", icon='ERROR')
                box.label(text="%d re-parented, %d added, %d removed"
                          % (len(moved), len(added), len(removed)))
                sync = box.row()
                sync.enabled = not active
                sync.operator(PC2_OT_sync_states.bl_idname, icon='FILE_REFRESH')
                if active:
                    box.label(text="Apply Base Pose to sync", icon='INFO')

        if not data["states"]:
            layout.label(text="New State records the current Base Pose first", icon='INFO')
        elif active:
            layout.label(text="Save the state, then Apply Base Pose to export", icon='INFO')
        else:
            layout.label(text="Pose changes here edit the export's base", icon='INFO')


class PC2_PT_files(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "Files"
    bl_parent_id = "PC2_PT_entity"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.pc2_entity

        col = layout.column(align=True)
        col.label(text="Model -- the primitive buttons")
        col.prop(props, "model_path", text="")
        prims = primitive_objects()
        layout.label(text="%d primitive(s) loaded" % len(prims),
                     icon='CHECKMARK' if prims else 'ERROR')
        if _autoload["error"]:
            layout.label(text=_autoload["error"], icon='ERROR')
        layout.operator(PC2_OT_load_primitives.bl_idname, icon='FILE_REFRESH')

        col = layout.column(align=True)
        col.label(text="Blueprints -- the entity list")
        col.prop(props, "entities_path", text="")

        col = layout.column(align=True)
        col.label(text="Levels -- the stage list")
        col.prop(props, "stages_path", text="")


class PC2_PT_stage(Panel):
    """A level: which entities stand where.

    Its own top-level panel rather than a child of Entity, because the two are
    alternating MODES -- one entity is on screen or one stage is, never both
    (see show_only) -- and a mode you switch into should not be filed inside
    the mode you are leaving.
    """
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "picoCAD2"
    bl_label = "Stage"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.pc2_entity
        active = props.stage

        box = layout.box()
        box.label(text="Stages", icon='OUTLINER_COLLECTION')
        names = stage_names(context)
        if not names:
            box.label(text="None yet", icon='INFO')
        else:
            col = box.column(align=True)
            for name in names:
                # Same icon language as the entity list: is this one already in
                # the .blend, or does clicking read it out of stages.js first.
                here = bpy.data.collections.get(name) is not None
                col.operator(
                    PC2_OT_edit_stage.bl_idname, text=name,
                    icon='OUTLINER_COLLECTION' if here else 'IMPORT',
                    depress=(active is not None and active.name == name),
                ).name = name
        box.operator(PC2_OT_new_stage.bl_idname, icon='ADD')

        if active is None:
            layout.box().label(text="Pick a stage above", icon='INFO')
            return

        box = layout.box()
        box.operator(PC2_OT_sign_guide.bl_idname)
        box.label(text="PLACEMENT* helpers never export")
        box.label(text="Reset guide after changing camera / aspect")
        placements = placements_of(active)
        box = layout.box()
        box.label(text="Place into %s (%d placement%s)"
                  % (active.name, len(placements), "" if len(placements) == 1 else "s"),
                  icon='OUTLINER_OB_GROUP_INSTANCE')
        entities = entity_names(context)
        if not entities:
            box.label(text="No entities yet", icon='INFO')
        else:
            grid = box.grid_flow(row_major=True, columns=2, even_columns=True)
            for name in entities:
                grid.operator(PC2_OT_place_entity.bl_idname, text=name).name = name
            box.label(text="lands at the 3D cursor")

        obj = context.view_layer.objects.active
        if is_placement(obj):
            box = layout.box()
            box.label(text="%s -- colour" % obj.instance_collection.name, icon='COLOR')
            current = part_color(obj)
            # A placement has no texture/tile mode to fall back to: the colour
            # is either overriding the whole entity or it is not there.
            draw_swatches(context, box, current, current >= 0)

            # The BANK is a different question from the colour: a colour makes
            # the whole entity one flat slot, a bank leaves its art alone and
            # swaps the 16 colours those indices mean.
            banks = game_banks(main_js_path(props))
            here = int(obj.get(BANK_PROP, 0) or 0)
            box.label(text="palette bank", icon='COLOR')
            row = box.row(align=True)
            for i in range(banks):
                row.operator(PC2_OT_set_bank.bl_idname,
                             text="stage" if i == 0 else str(i),
                             depress=(here == i)).index = i
            if banks == 1:
                box.label(text="main.js defines no PALETTE_BANKS", icon='INFO')
            elif here:
                # Blender previews bank 0's colours whatever is picked, and a
                # viewport that quietly disagrees with the game is worse than
                # one that says so. The View panel's palette dropdown is where
                # another 16 colours can actually be seen.
                box.label(text="viewport shows the stage's colours", icon='INFO')

        # The camera is not numbers to type: it is the PC2_Camera object, moved
        # by hand, and the fields behind it are read off the object by a timer.
        # What the panel shows is what you cannot see by looking -- whether the
        # frame is the game's, whether the camera can be, and what falls out.
        box = layout.box()
        box.label(text="Camera", icon='CAMERA_DATA')
        # The frame's SHAPE is the whole horizontal-or-vertical question, so it
        # gets buttons rather than two numbers to type.
        row = box.row(align=True)
        for label, w, h in ASPECTS:
            op = row.operator(PC2_OT_camera_aspect.bl_idname, text=label,
                              depress=(props.cam_res_x * h == props.cam_res_y * w))
            op.width, op.height = w, h
        row = box.row(align=True)
        row.prop(props, "cam_res_x")
        row.prop(props, "cam_res_y")

        box.operator(PC2_OT_stage_camera.bl_idname, icon='CAMERA_DATA')
        # The fields follow the camera OBJECT (a timer polls it), so moving the
        # camera by hand is authoring; what is left to say is whether the
        # result is the game's camera yet, and whether it can be.
        # Only when something is wrong: a camera that is the game's needs no
        # line saying so, and a .blend whose fields predate game_view.js needs
        # exactly one click.
        view = read_game_view(game_view_path(props))
        if view is None:
            box.label(text="no game_view.js beside stages.js", icon='ERROR')
        elif not camera_matches_game_view(props, view):
            row = box.row(align=True)
            row.label(text="differs from game_view.js", icon='ERROR')
            row.operator(PC2_OT_load_game_view.bl_idname, text="Load", icon='IMPORT')
        cam_obj = bpy.data.objects.get(CAMERA_NAME)
        if cam_obj is not None and cam_obj.type == 'CAMERA':
            roll = camera_fields(cam_obj, props.cam_dist)[3]
            if roll > 0.05:
                box.label(text="rolled %.1f deg -- the game cannot; Game Camera re-aims"
                          % roll, icon='ERROR')
            # The question a fixed camera makes worth asking, and the only one
            # it cannot answer for you. Only once the camera exists, since it
            # walks every placement's bounding box and a panel redrawing on
            # mouse-move should not do that until asked.
            shot, total, missing = stage_fit(context, active)
            if not total:
                box.label(text="nothing placed yet", icon='INFO')
            elif not missing:
                box.label(text="all %d placements in shot" % total, icon='CHECKMARK')
            else:
                box.label(text="%d of %d in shot" % (shot, total), icon='ERROR')
                box.label(text="out: %s" % ", ".join(sorted(set(missing))[:4]))

        # Export sits ABOVE the placement list, so it stays put however much is
        # placed -- a button that walks down the panel with every placement
        # ends up below the screen on exactly the levels that need it most.
        layout.operator(PC2_OT_export_stage.bl_idname, icon='EXPORT',
                        text="Export %s" % active.name)
        box = layout.box()
        box.label(text="%d placement%s" % (len(placements), "" if len(placements) == 1 else "s"),
                  icon='OUTLINER_OB_GROUP_INSTANCE')
        if placements:
            col = box.column(align=True)
            for obj in placements:
                entity = obj.instance_collection.name
                # The object's name is Blender's (CUBE.001) and the entity's is
                # the blueprint's; showing both only when they differ keeps a
                # renamed placement findable without doubling every row.
                text = entity if obj.name == entity else "%s  (%s)" % (entity, obj.name)
                color = part_color(obj)
                if color >= 0:
                    text += "  c%d" % color
                col.label(text=text,
                          icon='RADIOBUT_ON' if obj == context.view_layer.objects.active
                          else 'RADIOBUT_OFF')


classes = (
    PC2PartProps,
    PC2EntityProps,
    PC2_OT_load_primitives,
    PC2_OT_new_entity,
    PC2_OT_edit_entity,
    PC2_OT_new_stage,
    PC2_OT_edit_stage,
    PC2_OT_place_entity,
    PC2_OT_sign_guide,
    PC2_OT_stage_camera,
    PC2_OT_camera_aspect,
    PC2_OT_load_game_view,
    PC2_OT_export_stage,
    PC2_OT_add_part,
    PC2_OT_add_entity,
    PC2_OT_link_pose,
    PC2_OT_add_compound,
    PC2_OT_add_pivot,
    PC2_OT_drop_inherited_scale,
    PC2_OT_flat_preview,
    PC2_OT_mirror_parts,
    PC2_OT_set_color,
    PC2_OT_set_bank,
    PC2_OT_copy_palette,
    PC2_OT_pick_tile,
    PC2_OT_clear_uv,
    PC2_OT_new_state,
    PC2_OT_save_state,
    PC2_OT_apply_state,
    PC2_OT_apply_base,
    PC2_OT_sync_states,
    PC2_OT_delete_state,
    PC2_OT_export_entity,
    PC2_PT_view,
    PC2_PT_entity,
    PC2_PT_part,
    PC2_PT_states,
    PC2_PT_files,
    PC2_PT_stage,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pc2_entity = PointerProperty(type=PC2EntityProps)
    bpy.types.Object.pc2 = PointerProperty(type=PC2PartProps)
    subentities.register()
    if not bpy.app.timers.is_registered(camera_tick):
        bpy.app.timers.register(camera_tick, first_interval=0.5, persistent=True)
    if _on_blend_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_blend_loaded)


def unregister():
    if bpy.app.timers.is_registered(camera_tick):
        bpy.app.timers.unregister(camera_tick)
    subentities.unregister()
    from . import game_preview
    if bpy.app.timers.is_registered(game_preview.tick):
        bpy.app.timers.unregister(game_preview.tick)
    drop_palette_icons()
    drop_atlas_icon()
    if _on_blend_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_blend_loaded)
    del bpy.types.Object.pc2
    del bpy.types.Scene.pc2_entity
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
