"""Linked authoring assemblies. Only expanded ordinary parts reach the game."""
from contextlib import contextmanager
import json

import bpy
from mathutils import Matrix

MARK = 'pc2_subentity'
PREVIEW = 'pc2_subentity_preview'
POSE = 'pc2_subentity_pose'
MIRROR = 'pc2_subentity_mirror'


def api():
    import sys
    return sys.modules[__package__]


def is_link(obj):
    return bool(obj and obj.get(MARK))


def source_of(obj):
    source = obj.pc2_source
    if source is None:
        raise ValueError("'%s' has lost its source entity" % obj.name)
    return source


def link_label(obj):
    """`@SOURCE`, or `name @SOURCE` for a renamed link -- see LINK_LABEL_RE."""
    p = api()
    name = p.SUFFIX_RE.sub('', obj.name).strip()
    source = source_of(obj).name
    return '@' + source if name in (source, '', p.PIVOT_NAME) else '%s @%s' % (name, source)


def snapshot_item(obj):
    return {'s': str(obj.get(POSE, ''))} if is_link(obj) else {}


def validate(coll, trail=()):
    if coll in trail:
        raise ValueError('Entity reference cycle: ' + ' -> '.join(c.name for c in trail + (coll,)))
    for obj in coll.all_objects:
        if is_link(obj):
            validate(source_of(obj), trail + (coll,))


def snapshot(coll, pose=None):
    p = api()
    data = p.state_data(coll)
    objects = p.ordered_parts(coll)
    if data['states'] and (p.states_unstamped(data) or any(p.states_drift(data, objects))):
        raise ValueError("'%s': run Sync States to Hierarchy before using its poses" % coll.name)
    if pose:
        saved = next((s for s in data['states'] if s['name'] == pose), None)
        if saved is None:
            raise ValueError("'%s' has no pose '%s'" % (coll.name, pose))
        return p.resolve_state(coll, data, saved)
    if data['active']:
        return data['base']
    return p.entity_snapshot(coll)


def expand(coll, saved=None, prefix='', parent=None, visible=True, mirrored=False, trail=()):
    """Stable path UIDs keep each occurrence independent, including nested poses."""
    p = api()
    if coll in trail:
        raise ValueError('Entity reference cycle: ' + ' -> '.join(c.name for c in trail + (coll,)))
    saved = snapshot(coll) if saved is None else saved
    objects = p.ordered_parts(coll)
    ids = {o: prefix + p.part_uid(o) for o in objects}
    rows = []
    for obj in objects:
        item = saved.get(p.part_uid(obj))
        if item is None:
            raise ValueError("'%s': state predates '%s'; run Sync States to Hierarchy" % (coll.name, obj.name))
        local = p.matrix_from_values(item['m'])
        if mirrored:
            local = p.MIRROR_X @ local @ p.MIRROR_X
        uid = ids[obj]
        show = visible and item.get('v', True)
        rows.append((obj, uid, ids.get(obj.parent, parent), local, show))
        if is_link(obj):
            source = source_of(obj)
            pose = item.get('s', '')
            nested = expand(source, snapshot(source, pose), uid + '/', uid, show,
                            mirrored ^ bool(obj.get(MIRROR)), trail + (coll,))
            # Collection offsets are translation in the assembly's local frame.
            offset = p.conjugate(Matrix.Translation(-source.instance_offset))
            if mirrored ^ bool(obj.get(MIRROR)):
                offset = p.MIRROR_X @ offset @ p.MIRROR_X
            for child, key, owner, matrix, shown in nested:
                rows.append((child, key, owner, offset @ matrix if owner == uid else matrix, shown))
    return rows


def fill_collection(coll, rows, preview=False):
    p = api()
    copies = {}
    for i, (original, uid, parent, local, visible) in enumerate(rows):
        # A link's pivot is named with the `@SOURCE` marker so that importing
        # the file rebuilds the link rather than its expanded parts.
        obj = p.new_pivot(link_label(original)) if is_link(original) else original.copy()
        obj.parent = copies.get(parent)
        # `local` is parent_inverse @ basis. A part parented scale-free under a
        # non-uniformly scaled parent has a SHEARED local, which a TRS basis
        # cannot hold: assigning it as the basis makes Blender silently drop
        # the shear, so the copy's base pose came out exact while its states
        # (stored as raw locals) kept it -- "state contains shear" on a part
        # the base had passed. Keeping the original's parent inverse puts the
        # shear back where the exporter expects it, in the LOCAL, where it is
        # flattened to world space exactly as an unlinked entity's would be.
        inverse = Matrix.Identity(4) if is_link(original) else original.matrix_parent_inverse.copy()
        obj.matrix_parent_inverse = inverse
        obj.matrix_basis = inverse.inverted() @ p.conjugate(local)
        obj[p.UID_PROP] = uid
        obj[p.ORDER_PROP] = i
        # Copy names are suffixed by Blender, but export preserves the original label.
        obj[p.LABEL_PROP] = p.label_of(original) or ''
        coll.objects.link(obj)
        if preview and not visible:
            obj.hide_viewport = True
            obj.hide_render = True
        copies[uid] = obj
    return copies


def remove_collection(coll):
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(coll)


@contextmanager
def expanded_collection(context, coll):
    """Feed the existing exporter real Blender objects; never alter authored parts."""
    p = api()
    data = p.state_data(coll)
    if data['active']:
        raise ValueError('Apply Base Pose before exporting')
    base = snapshot(coll)
    rows = expand(coll, base)
    if len(rows) > 32:
        raise ValueError('Expanded entity has %d parts; visibility masks support at most 32 parts' % len(rows))
    # A hidden base part needs a runtime state; otherwise the runtime draws it.
    if any(not row[4] for row in rows):
        raise ValueError('Base pose contains hidden linked parts; use a character state for hidden parts')
    temp = bpy.data.collections.new('_PC2_expanded')
    context.scene.collection.children.link(temp)
    try:
        copies = fill_collection(temp, rows)
        context.view_layer.update()
        expanded = p.empty_state_data()
        expanded['base'] = p.entity_snapshot(temp)
        for state in data['states']:
            posed = expand(coll, p.resolve_state(coll, data, state))
            expanded['states'].append({'name': state['name'], 'parts': {
                uid: {'m': p.matrix_values(local), 'p': parent or '', 'v': show}
                for _, uid, parent, local, show in posed}})
        p.save_state_data(temp, expanded)
        yield temp
    finally:
        remove_collection(temp)


def refresh(obj):
    """A pose-specific collection is a disposable viewport cache, never a source."""
    p = api()
    source = source_of(obj)
    rows = expand(source, snapshot(source, str(obj.get(POSE, ''))),
                  mirrored=bool(obj.get(MIRROR)))
    fingerprint = json.dumps([(uid, parent, p.matrix_values(m), v, p.mesh_name_of(o),
                               p.active_color(o), p.uv_spec_of(o),
                               [slot.material.name if slot.material else '' for slot in o.material_slots])
                              for o, uid, parent, m, v in rows])
    old = obj.instance_collection
    if old and old.get(PREVIEW) == fingerprint:
        return
    new = bpy.data.collections.new('_PC2_link_preview')
    new[PREVIEW] = fingerprint
    new.instance_offset = source.instance_offset
    fill_collection(new, rows, preview=True)
    obj.instance_type = 'COLLECTION'
    obj.instance_collection = new
    # A link is an instancer, so placement_tint reads ITS object colour for
    # every part it shows -- and Blender's default is opaque white, which the
    # tint takes as an override in effect. Alpha 0 is how "no colour" is spelt.
    p.sync_instance_color(obj)
    if old and PREVIEW in old and old.users == 0:
        remove_collection(old)
    obj.pop('pc2_link_error', None)


def refresh_all():
    for obj in [o for o in bpy.data.objects if is_link(o)]:
        try:
            refresh(obj)
        except (ValueError, RuntimeError) as exc:
            obj['pc2_link_error'] = str(exc)


def tick():
    refresh_all()
    return 0.5


def register():
    bpy.types.Object.pc2_source = bpy.props.PointerProperty(type=bpy.types.Collection)
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    del bpy.types.Object.pc2_source
