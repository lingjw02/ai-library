# Three.js 0.180.0

Locally vendored from the existing `three@0.180.0` package. MIT license is in LICENSE.
Upstream: https://github.com/mrdoob/three.js/tree/r180

`three.module.js` and `three.core.js` are unchanged upstream distribution files.
`OrbitControls.js` is the upstream example addon with its single `from 'three'`
specifier changed to `from './three.module.js'` for native browser modules.
No CDN, external textures, build step, or runtime network dependency is required.
