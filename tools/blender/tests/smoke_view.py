"""Export the portrait camera and check its Blender frame against the engine."""
import json
import os
import re
import shutil
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H

pc2 = H.setup()
props = bpy.context.scene.pc2_entity
props.entities_path = H.ENTITIES
props.stages_path = H.out('portrait_stages.js')
shutil.copyfile(os.path.join(H.REPO, 'src', 'stages.js'), props.stages_path)
bpy.ops.pc2.load_primitives()
bpy.ops.pc2.edit_stage(name='INTRO')
props.cam_at = (0, 3.67, 4.62)
props.cam_yaw, props.cam_pitch, props.cam_dist, props.cam_fov = 0, 10, 20, 35
props.cam_res_x, props.cam_res_y = 600, 800
bpy.context.scene.render.pixel_aspect_x = 2
bpy.ops.pc2.stage_camera(look=False)
H.check('export stage and shared camera', bpy.ops.pc2.export_stage() == {'FINISHED'})
source = open(H.out('game_view.js'), encoding='utf-8').read()
body = source.split('= ', 1)[1].rstrip(';\n')
data = json.loads(re.sub(r',(\s*[}\]])', r'\1', re.sub(r'(\w+):', r'"\1":', body)))
H.check('exports the actual camera fields', data == {
    'width': 600, 'height': 800, 'at': [0, 3.67, 4.62],
    'yaw': 0, 'pitch': 10, 'dist': 20, 'fov': 35,
})
scene = bpy.context.scene
cam = scene.camera
H.check('square pixels', scene.render.pixel_aspect_x == scene.render.pixel_aspect_y == 1)
H.check('same clipping as runtime', abs(cam.data.clip_end - 50) < 1e-5)
view = (pc2.C @ cam.matrix_world).inverted()
with open(H.out('camera-portrait.json'), 'w', encoding='utf-8') as handle:
    json.dump({**data, 'aspect': data['width'] / data['height'], 'far': 50,
               'view': [view[row][col] for col in range(4) for row in range(4)],
               'frame': [list(pc2.conjugate_point(cam.matrix_world @ v))
                         for v in cam.data.view_frame(scene=scene)]}, handle)
bpy.ops.pc2.edit_stage(name='STAGE_1')
H.check('stage change keeps the fixed shot',
        max(abs(view[r][c] - (pc2.C @ cam.matrix_world).inverted()[r][c])
            for r in range(4) for c in range(4)) < 1e-6)
bpy.ops.pc2.export_stage()
H.check('exporting another stage rewrites the same shared camera',
        open(H.out('game_view.js'), encoding='utf-8').read() == source)
H.finish()
