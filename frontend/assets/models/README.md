# Authored orbital archive models

Ten original, independently authored GLB assets for AI Library. These are designed
mesh assemblies with family-specific silhouettes, armor, surface seams, archive
foliations, trusses, deck modules and communication equipment. They contain no
external art, textures, demo records or executable data. Shape and palette identify
purpose; decorative lights do not represent connection health or verified facts.

The geometry was authored reproducibly in Blender 4.5.3 LTS using
`scripts/build_universe_models.py`. An existing local Blender executable was used;
the Blender 4.4 directory had no executable. No software was installed or upgraded.
The GLBs are runtime files: Blender is not required to use the application.

## Build and verify

From the repository root, with an existing Blender executable on PATH:

```powershell
blender --background --python scripts/build_universe_models.py
python frontend/assets/models/verify_models.py
node frontend/assets/models/verify_browser.cjs
```

The browser verification uses the existing local `playwright-core` installation.
Set `PLAYWRIGHT_MODULE` to another existing installation when reproducing elsewhere.
It writes contact-sheet and JSON evidence under `.agent-tmp/stellar-models-20261012/`.
It checks actual GLB loading, all family mappings, normalized vertex bounds, shared
resource/private-transform ownership, local-centre roles, lightweight unpickable
previews, disposal, reload, loading failure and retry.

`manifest.json` records exact triangle counts, draw calls, byte lengths, materials,
authoring version and origin for every asset. There are 42,880 triangles and about
1.98 MB across the complete catalogue. Major assets have 3–7 material draws and
content assets 3–4; identical instances share GPU geometry and materials, but each
visible instance still incurs draw calls. A catalogue of 1,000 simultaneous objects
is not certified for any minimum frame rate by these asset tests.

## Application contract

`frontend/js/universe-models.js` exports:

- `loadUniverseModels()` returns one cached Promise resolving to
  `{status: 'ready', count: 10}`. Loading is atomic; failures reject and a later call
  retries. Disposal invalidates in-flight loads.
- `createUniverseModel(data, radius, {preview = false} = {})` returns a
  `THREE.Group`. Await readiness for full assets. `userData.modelFamily` and
  `userData.assetName` describe the selected asset; `nominalRadius` records the
  requested radius. Full models' farthest vertices lie on that radius.
- `disposeUniverseModels()` releases shared buffers/materials after all scene
  instances are removed. Do not dispose them per instance or per view navigation.

Transforms are unique per clone; geometry and materials are shared and immutable.
They carry `userData.sharedUniverseResource = true`. Per-instance selection effects
must use separate outlines/halos rather than changing the shared material.
`userData.movingParts` contains `{object, axis: 'y', speed}` entries on the personal
archive and station. Speeds are radians per second. The renderer owns the animation
loop and must pause these with reading mode / reduced motion. There is no hidden
asset animation loop or timer.

Previews are one very small geometry per family, with a disabled raycast; they
never clone full GLB meshes. Direct note, memory, Skill and MCP contents retain
their independently authored small models. Project title/domain keywords select
technical, creative or general knowledge worlds. This is a presentation heuristic,
not an inferred or persisted classification of the user's data.

## Dependencies and provenance

GLTFLoader and BufferGeometryUtils are the upstream Three.js `0.180.0` example
modules, copied from an existing verified local `three@0.180.0` package. Only their
module specifiers were changed to the adjacent `three.module.js` and utility file.
The existing `frontend/vendor/three/LICENSE` applies to those two dependencies.
All model geometry and model materials were authored for this project by the
checked-in build utility, with no input meshes or third-party artwork.

Asset verification is separate from integrated application QA and user visual
acceptance. The contact sheet demonstrates static appearance under ACES exposure
1.3 and studio lights; the application lighting and device performance need their
own integrated review.
