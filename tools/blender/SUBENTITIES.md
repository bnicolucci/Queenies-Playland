# Reusable entities

Reload the picoCAD2 add-on after updating its Python files.

1. Build a source entity such as `EYE_SIMPLE`. Use pivots for the pupil's look
   direction and eyelid hinges. Capture source states such as `closed` or
   `look_left` with the existing States panel.
2. Open a character. Use **Add Linked Entity** in the Entity panel and choose
   the source. Each copy is one selectable placement: move, rotate, scale,
   parent, duplicate, or use **Mirror Across X** as usual.
3. Select a copy. Its Part panel offers **Edit source** and a choice of source
   poses. Different copies can use different poses.
4. To animate those choices, create a character state, choose a pose on each
   copy, and save the character state. Apply Base Pose before exporting.
5. Source edits update the linked previews automatically. Export the source
   and any characters using it to update the game. Save the `.blend` to retain
   all authoring data.

References can nest. Export expands them into ordinary mesh parts and pivots,
with parent indices, visibility masks, and pose overrides remapped for each
occurrence. There is no new runtime entity format or runtime linking code.
Existing meshes remain shared; only ordinary part and pose descriptions are
added to the package.

Links carry free `// @SOURCE` comments on their exported placement pivots.
Blender can reconstruct links when importing those exports if the source is
available. The `.blend` remains the complete authoring source; the web entity
editor sees the expanded parts. Export source definitions too when transferring
entities to a fresh `.blend`.

The expanded entity may contain at most **32 parts, including pivots**. Missing
sources, missing named poses, and recursive references produce errors. After
changing a source's hierarchy, use **Sync States to Hierarchy** on that source.
Hidden parts belong in character states; a linked base pose with hidden parts
is refused because ordinary base blueprints do not carry visibility masks.

Tests: `bun run test:blender` includes `smoke_subentities` and a comparison
between its exported transforms and the actual browser entity runtime.
