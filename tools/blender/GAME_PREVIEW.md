# Game Preview (Blender 5.2)

Restart Blender after updating the addon (the installed `pc2_entity` directory
is junctioned to this repository). In the 3D View, open **N > picoCAD2**, select
an entity, then enable **Game Preview** beneath the primitive/parenting tools.
The toggle switches to Material Preview and Standard color management. Disable
it to return to the existing flat materials.

**Dither Pixel Size** defaults to 4, matching the current game's `PIXEL_SCALE`.
Color overrides, texture tiles/repeats, and the palette dropdown remain live.
Some palette slots have identical shade colors; those correctly stay solid.

The dev-only `pc2_entity/game_preview.py` builds copies of the existing materials,
retaining their UV and transparency graphs. Textures sample an index atlas and
a 16x3 palette built from model.txt's shade tables (extended indices masked to
15). The material uses the exact brightness curve, thresholds and checker phase
from `src/shaders/model.frag`, with the light converted to Blender coordinates.
One Newell normal per original polygon is stored on all its corners, preventing
Blender's internal triangulation from shading the halves of bent quads differently.
VECTOR object-to-world reproduces the game's non-uniform scale behavior.
Per-face `noshade` is an auxiliary mesh attribute mapped from importer face order.
The original materials and exported entity format are unchanged.

This is an authoring preview, not the game's framebuffer: Blender still
antialiases edges, and viewport geometry is not rendered at the game's reduced
resolution. Dither dimensions follow the largest 3D viewport every 0.25 seconds;
split views of different sizes share that reference. Camera renders are not
automatically synchronized to render resolution. The web entity editor remains
the exact game-rendering reference. Preview material state is shared across
scenes; use one authoring scene at a time.

Validation: `smoke_game_preview.py` runs Blender 5.2 EEVEE, tests toggling,
live color/UV/palette changes, and checks a rendered image for alternating
shade pixels, uniform shading across a bent quad, and saved shader-group upgrades.
It is included in `bun run test:blender`. As of this change the
older suite has existing fixture failures (removed sphere/cylinder primitives,
UNICORN round-trip/state expectations), reproduced against the unmodified HEAD
addon; the new preview test passes independently.
