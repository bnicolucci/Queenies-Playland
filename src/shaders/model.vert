#version 300 es
precision highp float;

// Fixed locations match engine.js's VAO layout. mat4 consumes slots 5–8.
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec2 a_texCoord;
layout(location = 3) in float a_colorIndex;
layout(location = 4) in float a_faceFlags;
// Per-instance (divisor 1): world matrix, UV retile dest rect (origin.xy,
// size.zw), params = (color override, uv mode, repeatU, repeatV).
// uv mode: 0 = untouched, 1 = repeat within the mesh's own rect, 2 = remap
// into a_uvRect.
layout(location = 5) in mat4 a_world;
layout(location = 9) in vec4 a_uvRect;
layout(location = 10) in vec4 a_params;

uniform mat4 u_projView;

out vec3 v_normal;
out vec2 v_texCoord;
out float v_colorIndex;
out float v_faceFlags;
flat out vec4 v_uvRect;
flat out vec4 v_params;

void main() {
    gl_Position = u_projView * a_world * vec4(a_position, 1.0);

    v_normal = mat3(a_world) * a_normal;
    v_texCoord = a_texCoord;
    v_colorIndex = a_colorIndex;
    v_faceFlags = a_faceFlags;
    v_uvRect = a_uvRect;
    v_params = a_params;
}
