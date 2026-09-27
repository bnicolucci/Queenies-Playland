"""Dev-only palette/dither materials. Originals remain intact for Flat Preview."""
import hashlib
import json
import math

import bpy
from mathutils import Vector

GROUP = 'pc2_game_shading'
ATTR = 'pc2_game_noshade'
NORMAL_ATTR = 'pc2_game_face_normal'
SHADING_VERSION = 2
_cache = {}


def reset():
    _cache.clear()


def assets(pc2):
    props = bpy.context.scene.pc2_entity
    path = bpy.path.abspath(props.model_path)
    import os
    key = (path, os.path.getmtime(path), tuple(pc2.scene_palette(bpy.context)))
    if _cache.get('key') == key:
        return _cache
    with open(path, encoding='utf-8') as handle:
        model = json.load(handle)
    tex = model['texture']
    colors = key[2]
    rows = [list(range(16)), tex['shade_pal_1'], tex['shade_pal_2']]
    def image(name, w, h, pixels):
        im = bpy.data.images.get(name)
        if im is None:
            im = bpy.data.images.new(name, width=w, height=h, alpha=True, float_buffer=True)
        if tuple(im.size) != (w, h):
            im.scale(w, h)
        im.colorspace_settings.name = 'Non-Color'
        im.pixels.foreach_set(pixels)
        im.update()
        im.pack()
        return im
    palette = image('pc2_game_palette', 16, 3,
                    [v for row in rows for i in row
                     for v in (*[pc2.srgb_to_linear(round(c * 255) / 255)
                                 for c in colors[int(i) & 15]], 1.0)])
    pixels = tex['pixels']
    w = 128
    h = len(pixels) // w
    transparent = tex.get('transparent_color', 0)
    index = image('pc2_game_indices', w, h,
                  [v for y in reversed(range(h)) for x in range(w)
                   for v in (int(pixels[y*w+x], 16) / 15,) * 3
                   + (float(int(pixels[y*w+x], 16) != transparent),)])
    faces = {}
    def walk(node):
        if node.get('mesh'):
            faces[node.get('name')] = [f for f in node['mesh']['faces']
                                       if len(f.get('vertex_ids', [])) >= 3]
        for child in node.get('children', []):
            walk(child)
    walk(model['graph'])
    _cache.update(key=key, palette=palette, index=index, faces=faces)
    return _cache


def shading_group():
    group = bpy.data.node_groups.get(GROUP)
    if group and group.get('pc2_version') == SHADING_VERSION:
        return group
    if group is None:
        group = bpy.data.node_groups.new(GROUP, 'ShaderNodeTree')
        group.interface.new_socket(name='Row', in_out='OUTPUT', socket_type='NodeSocketFloat')
    # Upgrade saved .blend materials in place: every existing material retains
    # its link to this group, including cached game-preview copies.
    group.nodes.clear()
    group['pc2_version'] = SHADING_VERSION
    nt = group
    def node(kind):
        return nt.nodes.new(kind)
    def feed(value, socket):
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            nt.links.new(value, socket)
    def math_node(op, a, b=0):
        n = node('ShaderNodeMath')
        n.operation = op
        feed(a, n.inputs[0]); feed(b, n.inputs[1])
        return n.outputs[0]
    geom = node('ShaderNodeNewGeometry')
    # True Normal describes the RENDER TRIANGLE, which splits a non-planar
    # quad diagonally. Use the original polygon's Newell normal on every corner.
    local = node('ShaderNodeAttribute'); local.attribute_name = NORMAL_ATTR
    world = node('ShaderNodeVectorTransform')
    world.vector_type = 'VECTOR'; world.convert_from = 'OBJECT'; world.convert_to = 'WORLD'
    nt.links.new(local.outputs['Vector'], world.inputs[0])
    facing = node('ShaderNodeVectorMath'); facing.operation = 'SCALE'
    nt.links.new(world.outputs[0], facing.inputs[0])
    nt.links.new(math_node('SUBTRACT', 1, math_node('MULTIPLY', 2, geom.outputs['Backfacing'])),
                 facing.inputs['Scale'])
    normal = node('ShaderNodeVectorMath'); normal.operation = 'NORMALIZE'
    nt.links.new(facing.outputs[0], normal.inputs[0])
    dot = node('ShaderNodeVectorMath'); dot.operation = 'DOT_PRODUCT'
    nt.links.new(normal.outputs[0], dot.inputs[0])
    length = math.sqrt(.3**2 + .8**2 + .5**2)
    dot.inputs[1].default_value = (-.3/length, .5/length, .8/length)
    inv = math_node('SUBTRACT', 1, dot.outputs['Value'])
    light = math_node('MAXIMUM', .3, math_node('SUBTRACT', 1, math_node('MULTIPLY', inv, inv)))
    coord = node('ShaderNodeTexCoord')
    sep = node('ShaderNodeSeparateXYZ'); nt.links.new(coord.outputs['Window'], sep.inputs[0])
    dims = []
    for axis in ('X', 'Y'):
        size = node('ShaderNodeValue'); size.name = 'Pixels ' + axis
        size.outputs[0].default_value = 256
        dims.append(math_node('FLOOR', math_node('MULTIPLY', sep.outputs[axis], size.outputs[0])))
    parity = math_node('MODULO', math_node('ADD', *dims), 2)
    # Exactly model.frag's ladder, including its phase reversal at 0.4.
    dark = math_node('ADD', 1, parity)
    middle = math_node('SUBTRACT', 2, parity)
    bright = math_node('SUBTRACT', 1, parity)
    def select(condition, yes, no):
        return math_node('ADD', math_node('MULTIPLY', condition, yes),
                         math_node('MULTIPLY', math_node('SUBTRACT', 1, condition), no))
    row = select(math_node('LESS_THAN', light, .4), dark,
                 select(math_node('LESS_THAN', light, .56), middle,
                        select(math_node('LESS_THAN', light, .75), bright, 0)))
    flag = node('ShaderNodeAttribute'); flag.attribute_name = ATTR
    row = math_node('MULTIPLY', row, math_node('SUBTRACT', 1, flag.outputs['Fac']))
    out = node('NodeGroupOutput'); nt.links.new(row, out.inputs['Row'])
    return group


def apply(pc2, obj):
    if obj.type != 'MESH' or not pc2.mesh_name_of(obj):
        return
    data = assets(pc2)
    shading_group()
    normals = obj.data.attributes.get(NORMAL_ATTR)
    if normals is None:
        normals = obj.data.attributes.new(NORMAL_ATTR, 'FLOAT_VECTOR', 'CORNER')
    for poly in obj.data.polygons:
        points = [obj.data.vertices[i].co for i in poly.vertices]
        normal = Vector((0, 0, 0))
        for a, b in zip(points, points[1:] + points[:1]):
            normal.x += (a.y - b.y) * (a.z + b.z)
            normal.y += (a.z - b.z) * (a.x + b.x)
            normal.z += (a.x - b.x) * (a.y + b.y)
        normal.normalize()
        for loop in poly.loop_indices:
            normals.data[loop].vector = normal
    faces = data['faces'].get(pc2.mesh_name_of(obj), [])
    attr = obj.data.attributes.get(ATTR)
    if attr is None:
        attr = obj.data.attributes.new(ATTR, 'FLOAT', 'FACE')
    for poly, value in zip(obj.data.polygons, attr.data):
        value.value = float(bool(faces[poly.index].get('noshade'))) if poly.index < len(faces) else 0
    override = pc2.part_color(obj) if obj.pc2.draws == 'COLOR' else -1
    for slot in obj.material_slots:
        base = slot.material
        if base is None:
            continue
        key = 'pc2game_' + hashlib.md5((base.name + '|' + str(override)).encode()).hexdigest()[:16]
        mat = bpy.data.materials.get(key)
        if mat is None:
            mat = base.copy(); mat.name = key
            nt = mat.node_tree
            emission = next((n for n in nt.nodes if n.type == 'EMISSION'), None)
            if emission is None:
                bpy.data.materials.remove(mat)
                continue
            tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE'), None)
            def math_node(op, a, b):
                n = nt.nodes.new('ShaderNodeMath'); n.operation = op
                for value, inp in zip((a, b), n.inputs):
                    if isinstance(value, (int, float)): inp.default_value = value
                    else: nt.links.new(value, inp)
                return n.outputs[0]
            if override >= 0:
                index = override
            elif tex:
                tex.image = data['index']; tex.interpolation = 'Closest'
                index = math_node('MULTIPLY', tex.outputs['Color'], 15)
            else:
                match = pc2.COLOR_MAT_RE.search(base.name)
                index = int(match.group(1)) & 15 if match else 0
            u = math_node('DIVIDE', math_node('ADD', index, .5), 16)
            shade = nt.nodes.new('ShaderNodeGroup'); shade.node_tree = shading_group()
            v = math_node('DIVIDE', math_node('ADD', shade.outputs['Row'], .5), 3)
            uv = nt.nodes.new('ShaderNodeCombineXYZ')
            nt.links.new(u, uv.inputs['X']); nt.links.new(v, uv.inputs['Y'])
            pal = nt.nodes.new('ShaderNodeTexImage'); pal.image = data['palette']
            pal.interpolation = 'Closest'; pal.extension = 'EXTEND'
            nt.links.new(uv.outputs[0], pal.inputs['Vector'])
            nt.links.new(pal.outputs['Color'], emission.inputs['Color'])
        # This material is a COPY of one that already carried the tint, and the
        # palette lookup above was just linked over it -- placement_tint checks
        # the link rather than the node, so asking again re-inserts the mix.
        pc2.placement_tint(mat)
        slot.link = 'OBJECT'; slot.material = mat
    obj.update_tag()


def tick():
    if not hasattr(bpy.context.scene, 'pc2_entity'):
        return None
    group = bpy.data.node_groups.get(GROUP)
    props = bpy.context.scene.pc2_entity
    if group and props.game_preview:
        areas = [a for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == 'VIEW_3D']
        if areas:
            area = max(areas, key=lambda a: a.width * a.height)
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region:
                for axis, size in zip(('X', 'Y'), (region.width, region.height)):
                    socket = group.nodes['Pixels ' + axis].outputs[0]
                    value = max(1, size // props.game_pixel_scale)
                    if socket.default_value != value:
                        socket.default_value = value
    return .25
