#version 300 es
precision highp float;

in vec3 v_normal;
in vec2 v_texCoord;
in float v_colorIndex;
in float v_faceFlags;
flat in vec4 v_uvRect;
flat in vec4 v_params;

uniform sampler2D u_indexTexture;
uniform sampler2D u_paletteTexture;
uniform float u_transparentColor;
uniform vec4 u_uvSrc;   // this mesh's own UV bounds (origin.xy, size.zw)

out vec4 fragColor;

// Per-instance UV retile: normalize the sampled UV within the mesh's own
// rect, repeat, then map into the dest rect (mode 2) or back into the own
// rect (mode 1 = plain repeat).
vec2 retile(vec2 t) {
    if (v_params.y < 0.5) return t;
    vec2 l = fract((t - u_uvSrc.xy) / max(u_uvSrc.zw, vec2(1e-6)) * v_params.zw);
    vec4 d = v_params.y > 1.5 ? v_uvRect : u_uvSrc;
    return d.xy + l * d.zw;
}

void main() {
    int flags = int(v_faceFlags + 0.5);
    bool noShade = (flags & 1) != 0;
    bool noTex = (flags & 2) != 0;

    // A per-instance colour override replaces every texel the mesh could
    // sample, so there is nothing left to look up -- and SKIPPING the lookup is
    // what makes an overridden instance a solid shape instead of the texture's
    // silhouette in one colour. This also keeps unpainted model meshes solid;
    // overridden HUD glyphs (wipe panels) become solid rectangles.
    int colorIdx = int(v_params.x);
    // An override of 16 + slot means UNSHADED: the flat palette colour with no
    // dither ladder at all, which is as close to emissive as a palette engine
    // gets -- the palette entry is the ceiling, since nothing here blends or
    // adds light. It says per INSTANCE what the mesh's own faceFlags bit0 says
    // per face, and it rides in the override float that was already there, so
    // the vertex layout, the divisors and the varyings are untouched.
    if (colorIdx > 15) {
        colorIdx -= 16;
        noShade = true;
    }
    if (colorIdx < 0) {
        if (noTex) {
            colorIdx = int(v_colorIndex);
        } else {
            colorIdx = int(texture(u_indexTexture, retile(v_texCoord)).r * 255.0 + 0.5);
            if (colorIdx == int(u_transparentColor)) {
                discard;
            }
        }
    }

    // Compute shading level
    int paletteRow = 0;
    if (!noShade) {
        vec3 normal = normalize(v_normal);
        if (gl_FrontFacing) normal = -normal;
        
        float rawDot = -dot(normal, normalize(vec3(0.3, 0.8, 0.5)));
        float lightFactor = 1.0 - (1.0 - rawDot) * (1.0 - rawDot);
        // Only band comparisons consume this value; clamping to 0.3..1
        // cannot change which side of 0.4, 0.56 or 0.75 it falls on.

        // One checkerboard for the whole ladder: each band mixes the same
        // two-tone pattern, only in different pairs of palette rows, so
        // spelling `mod(...)` per branch is three chances to disagree about
        // what the dither IS. The mod is 0 or 1, so `on` is every other
        // pixel. Widening the modulus here turns the checker into a
        // 45-degree stripe (lines of constant x+y) -- measured, and the
        // checker won; see Saving future bytes in CLAUDE.md.
        bool on = mod(floor(gl_FragCoord.x) + floor(gl_FragCoord.y), 2.0) < 0.5;
        if (lightFactor < 0.4) {
            paletteRow = on ? 1 : 2;
        } else if (lightFactor < 0.56) {
            paletteRow = on ? 2 : 1;
        } else if (lightFactor < 0.75) {
            paletteRow = on ? 1 : 0;
        }
    }

    vec3 color = texelFetch(u_paletteTexture, ivec2(colorIdx, paletteRow), 0).rgb;

    fragColor = vec4(color, 1.0);
}
