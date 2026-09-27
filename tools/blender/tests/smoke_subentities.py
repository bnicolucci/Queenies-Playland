"""Linked eyes: independent poses, nested export, live edits and no runtime format changes."""
import os
import sys
import json

import bpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

p = H.setup()
s = p.subentities
ctx = bpy.context
props = ctx.scene.pc2_entity
props.entities_path = H.out('entities_subentities.js')
with open(props.entities_path, 'w') as f:
    f.write(p.DEFAULT_HEADER)
bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name='EYE')
eye = props.entity
props.parent_to_active = False
bpy.ops.pc2.add_pivot()
look = ctx.view_layer.objects.active
look.name = 'look'
props.parent_to_active = True
bpy.ops.pc2.add_part(mesh_name='mesh_hemisphere')
pupil = ctx.view_layer.objects.active
pupil.name = 'pupil'
pupil.location = (0, 0.4, 0)
pupil.scale = (0.2, 0.2, 0.2)
ctx.view_layer.update()
bpy.ops.pc2.new_state(name='LOOK_LEFT')
look.rotation_euler.z = 0.4
ctx.view_layer.update()
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
bpy.ops.pc2.new_state(name='BLINK')
pupil.hide_set(True)
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()

bpy.ops.pc2.new_entity(name='FACE')
face = props.entity
props.parent_to_active = False
bpy.ops.pc2.add_entity(name='EYE')
left = ctx.view_layer.objects.active
left.name = 'left_eye'
left.location.x = -1
bpy.ops.pc2.add_entity(name='EYE')
right = ctx.view_layer.objects.active
right.name = 'right_eye'
right.location.x = 1
ctx.view_layer.update()
H.check('two copies retain the source', left.pc2_source == eye and right.pc2_source == eye)
# A link is a collection instancer, so placement_tint reads ITS object colour
# for every part it shows. Blender's default is opaque white, and alpha is the
# tint's "override in effect" flag -- so an unstamped link drew every sub-entity
# white. new_pivot() does not touch obj.color; refresh() must.
H.check('links carry no colour override (alpha 0, not white)',
        all(o.color[3] == 0.0 for o in (left, right)))
H.check('link menu lists every other entity',
        [i[0] for i in p.link_entity_items(None, ctx)] == ['EYE'])
H.check('references are exported pivots', p.is_part(left) and p.is_pivot(left))
H.check('preview collections are not entity library entries',
        all(s.PREVIEW not in c for c in p.entity_collections()))

bpy.ops.pc2.new_state(name='WINK')
bpy.ops.pc2.link_pose(name='BLINK')
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
H.check('base restores independent pose', right.get(s.POSE) == '')
bpy.ops.pc2.new_state(name='LOOK')
bpy.ops.pc2.link_pose(name='LOOK_LEFT')
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
H.check('export expands six ordinary parts', bpy.ops.pc2.export_entity() == {'FINISHED'})
text = open(props.entities_path, encoding='utf-8').read()
bp = p.parse_blueprint(text, 'FACE')
states = p.parse_states(text, 'FACE')
H.check('six parts including independent roots', len(bp) == 6 and bp[2]['parent'] == 1 and bp[5]['parent'] == 4)
H.check('wink hides only right pupil', states[0][0] == 0b011111)
H.check('look moves only right look pivot', [d[0] for d in states[1][1]] == [4])
H.check('links stay in the blend', len(p.ordered_parts(face)) == 2 and left.pc2_source == eye)
H.check('source state untouched', p.state_data(eye)['active'] == '')

# Real runtime verification fixture, with independently composed world matrices.
expected = []
for link in (left, right):
    root = link.matrix_world
    for m in (root, root @ look.matrix_local, root @ look.matrix_local @ pupil.matrix_local):
        engine = p.conjugate(m)
        expected.append([engine[r][c] for c in range(4) for r in range(4)])
with open(H.out('expected_subentities.json'), 'w') as f:
    json.dump({'names': ['left', 'look', 'pupil', 'right', 'look', 'pupil'], 'matrices': expected}, f)

H.section('source edits propagate and keep placement')
p.focus_entity(ctx, eye)
pupil.location.y = 0.6
ctx.view_layer.update()
p.focus_entity(ctx, face)
s.refresh_all()
for link in (left, right):
    copy = next(o for o in link.instance_collection.objects if p.mesh_name_of(o))
    H.check('updated pupil in ' + link.name, abs(copy.location.y - 0.6) < 1e-5)
H.check('placement preserved', left.location.x == -1 and right.location.x == 1)

# A state pins only the parts that were POSED in it; everything else follows
# the base. Otherwise enlarging the pupil in the base shrinks it back in every
# blink, and a link parked on a pose keeps showing the eye as it was when the
# pose was saved -- the bug this section exists for. BLINK only hides the
# pupil; LOOK_LEFT only turns the look pivot. Neither owns the pupil's size.
H.section('unposed parts follow the base into every state and posed link')
p.focus_entity(ctx, face)
ctx.view_layer.objects.active = left
bpy.ops.pc2.link_pose(name='LOOK_LEFT')      # park the left eye on a pose
p.focus_entity(ctx, eye)
pupil.scale = (pupil.scale.x * 3, pupil.scale.y * 3, pupil.scale.z * 3)
ctx.view_layer.update()
big = tuple(round(v, 4) for v in pupil.scale)
p.focus_entity(ctx, face)
s.refresh_all()
copy = next(o for o in left.instance_collection.objects if p.mesh_name_of(o))
H.check('a link parked on a pose shows the base edit',
        tuple(round(v, 4) for v in copy.matrix_basis.to_scale()) == big,
        '%s vs %s' % (tuple(round(v, 4) for v in copy.matrix_basis.to_scale()), big))
H.check('and still wears the pose',
        abs(next(o for o in left.instance_collection.objects if o.get(p.LABEL_PROP) == 'look')
            .matrix_basis.to_euler().z - 0.4) < 1e-4)
p.focus_entity(ctx, eye)
bpy.ops.pc2.apply_state(name='LOOK_LEFT')
H.check('applying a state keeps the base edit on its unposed parts',
        tuple(round(v, 4) for v in pupil.scale) == big)
bpy.ops.pc2.apply_base()
bpy.ops.pc2.export_entity()
text = open(props.entities_path, encoding='utf-8').read()
eye_states = p.parse_states(text, 'EYE')
H.check('export pins no part the author never posed',
        all(all(d[0] != 1 for d in st[1]) for st in eye_states)   # 1 = pupil's index in EYE
        and [d[0] for d in eye_states[0][1]] == [0],   # LOOK_LEFT turns only the look pivot
        str(eye_states))
p.focus_entity(ctx, face)
ctx.view_layer.objects.active = left
bpy.ops.pc2.link_pose(name='')

H.section('nested references and cycle refusal')
bpy.ops.pc2.new_entity(name='CHARACTER')
character = props.entity
bpy.ops.pc2.add_entity(name='FACE')
head = ctx.view_layer.objects.active
head.location.z = 2
head.scale = (2, 2, 2)
ctx.view_layer.update()
bpy.ops.pc2.new_state(name='WINK')
bpy.ops.pc2.link_pose(name='WINK')
bpy.ops.pc2.save_state()
bpy.ops.pc2.apply_base()
bpy.ops.pc2.export_entity()
text = open(props.entities_path, encoding='utf-8').read()
H.check('nested link expands seven parts', len(p.parse_blueprint(text, 'CHARACTER')) == 7)
H.check('nested wink remaps mask', p.parse_states(text, 'CHARACTER')[0][0] == 0b0111111)
try:
    s.validate(character, (eye,))
    refused = False
except ValueError:
    refused = True
H.check('cycles rejected before insertion', refused)

H.section('links round trip through the file, into a .blend that has none of it')
# The file is flat, but a link's pivot is labelled `@SOURCE`, so importing it
# into a fresh .blend rebuilds the link (importing the source first) instead
# of five dead parts -- and reads each state's pose back off the numbers.
text = open(props.entities_path, encoding='utf-8').read()
before = p.entity_block(text, 'CHARACTER')
before_states = p.state_block(text, 'CHARACTER')
H.check('a link exports as a marked pivot', '// @FACE' in before and '@EYE' in p.entity_block(text, 'FACE'))
H.check('a renamed link keeps its name in the marker',
        '// left_eye @EYE' in p.entity_block(text, 'FACE'), p.entity_block(text, 'FACE'))
for c in (character, face, eye):
    c.name += '_AUTHORED'      # so the import cannot find them and must build all three
fresh, n = p.build_entity_collection(ctx, 'CHARACTER')
p.focus_entity(ctx, fresh)
H.check('CHARACTER imports as one link, not seven parts', n == 1, str(n))
head2 = next(o for o in p.ordered_parts(fresh) if s.is_link(o))
H.check('the link points at a freshly imported FACE',
        head2.pc2_source is not None and head2.pc2_source.name == 'FACE'
        and bpy.data.collections.get('EYE') is not None)
face2 = head2.pc2_source
H.check('no state fell back to the base pose', not head2.get('pc2_link_note'), str(head2.get('pc2_link_note')))
eyes2 = [o for o in p.ordered_parts(face2) if s.is_link(o)]
H.check('FACE imports as two eye links', len(eyes2) == 2 and all(o.pc2_source.name == 'EYE' for o in eyes2),
        str([o.name for o in p.ordered_parts(face2)]))
d = p.state_data(fresh)
wink2 = next(st for st in d['states'] if st['name'] == 'WINK')
H.check('the WINK state reads back as the link posed WINK',
        wink2['parts'][p.part_uid(head2)].get('s') == 'WINK' and wink2['parts'][p.part_uid(head2)].get('d'))
dface = p.state_data(face2)
H.check("FACE's WINK reads back as the right eye posed BLINK",
        sorted(st['parts'][p.part_uid(o)].get('s', '') for st in dface['states'] if st['name'] == 'WINK'
               for o in eyes2) == ['', 'BLINK'])
bpy.ops.pc2.export_entity()
after = open(props.entities_path, encoding='utf-8').read()
H.check('the linked entity round trips byte-identically', p.entity_block(after, 'CHARACTER') == before)
H.check('and so do its poses', p.state_block(after, 'CHARACTER') == before_states)
# The point of it all: an edit to the fresh EYE reaches CHARACTER through two links.
eye2 = bpy.data.collections['EYE']
pupil2 = next(o for o in p.ordered_parts(eye2) if p.mesh_name_of(o))
p.focus_entity(ctx, eye2)
pupil2.scale = (pupil2.scale.x * 5, pupil2.scale.y * 5, pupil2.scale.z * 5)
ctx.view_layer.update()
p.focus_entity(ctx, fresh)
s.refresh_all()
bpy.ops.pc2.export_entity()
after = open(props.entities_path, encoding='utf-8').read()
H.check('a source edit in the fresh .blend reaches the linked entity',
        'scale: [%s, ' % p.num(pupil2.scale.x) in p.entity_block(after, 'CHARACTER'),
        p.entity_block(after, 'CHARACTER'))
character, face, eye, head = fresh, face2, eye2, head2

H.section('missing poses and 32-part limit are explicit errors')
head[s.POSE] = 'DELETED'
try:
    with s.expanded_collection(ctx, character):
        pass
    refused = False
except ValueError as exc:
    refused = 'DELETED' in str(exc)
H.check('missing pose does not silently reset', refused)
head[s.POSE] = ''
p.focus_entity(ctx, face)
for i in range(10):
    bpy.ops.pc2.add_entity(name='EYE')
try:
    with s.expanded_collection(ctx, face):
        pass
    refused = False
except ValueError:
    refused = True
H.check('oversized/drifted entity refused', refused)
H.finish()
