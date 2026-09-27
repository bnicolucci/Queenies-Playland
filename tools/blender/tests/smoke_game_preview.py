"""Exercise preview toggling, live edits, palette swaps and a real EEVEE render."""
import os
import sys
import bpy
from mathutils import Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H
pc2 = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.out('preview_entities.js')
bpy.ops.pc2.load_primitives()
bpy.ops.pc2.new_entity(name='PREVIEWTEST')
bpy.ops.pc2.add_part(mesh_name='mesh_hemisphere')
obj = bpy.context.object
obj[pc2.COLOR_PROP] = 2
obj.pc2.draws = 'COLOR'
obj.scale = (1.8, 1, 1.4)
base = obj.material_slots[0].material
props.game_preview = True
H.check('game material applied', obj.material_slots[0].material.name.startswith('pc2game_'))
from pc2_entity import game_preview as gp
H.check('flat face flags available', gp.ATTR in obj.data.attributes)
first = obj.material_slots[0].material
obj[pc2.COLOR_PROP] = 8
pc2.refresh_part_preview(obj)
H.check('live color changes', obj.material_slots[0].material != first)
props.game_preview = False
H.check('flat material restored', not obj.material_slots[0].material.name.startswith('pc2game_'))
obj.pc2.draws = 'TILE'
props.game_preview = True
H.check('tile retains retile math', any(n.type == 'VECT_MATH' for n in obj.material_slots[0].material.node_tree.nodes))
H.check('index atlas used', any(n.type == 'TEX_IMAGE' and n.image == gp.assets(pc2)['index'] for n in obj.material_slots[0].material.node_tree.nodes))
palette_before = list(gp.assets(pc2)['palette'].pixels)
# A LIBRARY palette, not merely 'not the model': PALETTE_ITEMS also holds
# "this stage's mood", which with no stage picked resolves to the model's
# own colours -- so the texture would be unchanged and this would read as
# a broken swap.
props.palette = next(item[0] for item in pc2.PALETTE_ITEMS if item[0] in pc2.PALETTE_COLORS)
H.check('palette swap updates shade texture', list(gp.assets(pc2)['palette'].pixels) != palette_before)
props.palette = pc2.PALETTE_MODEL
obj[pc2.COLOR_PROP] = 2
obj.pc2.draws = 'COLOR'
# Use fixed dither dimensions for this camera render (the UI timer tracks viewport sizes).
scene = bpy.context.scene
for other in list(scene.objects):
    if other != obj:
        bpy.data.objects.remove(other, do_unlink=True)
obj.hide_render = False
for coll in obj.users_collection:
    coll.hide_render = False
# EEVEE compiles and exercises the actual material graph, including Window coordinates.
scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
for axis in ('X', 'Y'):
    gp.shading_group().nodes['Pixels ' + axis].outputs[0].default_value = 128
bpy.ops.object.camera_add(location=(3, -5, 3))
camera = bpy.context.object
camera.rotation_euler = (Vector((0, 0, .25)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type = 'ORTHO'; camera.data.ortho_scale = 3.5
scene.camera = camera
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = H.out('game_preview.png')
bpy.ops.render.render(write_still=True)
H.check('render written', os.path.exists(scene.render.filepath))
render = bpy.data.images.load(scene.render.filepath, check_existing=False)
pixels = list(render.pixels)
def rgb(x, y):
    start = (y * 512 + x) * 4
    return tuple(round(v * 255) for v in pixels[start:start+3])
checks = 0
for y in range(130, 350, 4):
    for x in range(130, 350, 4):
        a, b, c, d = rgb(x,y), rgb(x+4,y), rgb(x,y+4), rgb(x+4,y+4)
        if a == d and b == c and sum(abs(u-v) for u,v in zip(a,b)) > 20:
            checks += 1
H.check('render contains alternating shade pixels', checks > 20, str(checks))
bpy.data.images.remove(render)
props.game_preview = False
H.check('toggle restores all slots', all(not s.material.name.startswith('pc2game_') for s in obj.material_slots))
# A deliberately bent quad: render triangles have very different normals.
# One original face must still choose one shade pair across its diagonal.
mesh = bpy.data.meshes.new('warped_quad')
mesh.from_pydata([(-1,-1,0), (1,-1,0), (1,1,4), (-1,1,0)], [], [(0,1,2,3)])
mesh.materials.append(base)
obj.data = mesh
obj.scale = (1,1,1)
obj.rotation_euler = (0,0,0)
obj.location = (0,0,0)
props.game_preview = True
normals = mesh.attributes[gp.NORMAL_ATTR]
H.check('quad corners share the polygon normal',
        all((value.vector - Vector((-1,-1,1)).normalized()).length < 1e-6 for value in normals.data))
# Also exercise migration of a saved shader group used by cached materials.
group = gp.shading_group()
group['pc2_version'] = 1
pc2.refresh_part_preview(obj)
H.check('saved shader group upgraded in place', gp.shading_group() == group
        and group.get('pc2_version') == gp.SHADING_VERSION)
for axis in ('X', 'Y'):
    group.nodes['Pixels ' + axis].outputs[0].default_value = 128
camera.location = (0,0,8)
camera.rotation_euler = (0,0,0)
scene.render.filepath = H.out('game_preview_quad.png')
bpy.ops.render.render(write_still=True)
render = bpy.data.images.load(scene.render.filepath, check_existing=False)
pixels = list(render.pixels)
samples = {rgb(x,y) for y in range(150,350,8) for x in range(150,350,8)}
spread = max(max(c[k] for c in samples) - min(c[k] for c in samples) for k in range(3))
H.check('no diagonal shade split across a bent quad', spread <= 4, str(samples))
bpy.data.images.remove(render)
triangles = bpy.data.meshes.new('authored_triangles')
triangles.from_pydata([(-1,-1,0), (1,-1,0), (1,1,4), (-1,1,0)], [], [(0,1,2), (0,2,3)])
triangles.materials.append(base)
obj.data = triangles
pc2.refresh_part_preview(obj)
normals = triangles.attributes[gp.NORMAL_ATTR]
H.check('separate triangles retain separate normals',
        (normals.data[0].vector - normals.data[3].vector).length > .5)
H.finish()
