"""Structural acceptance for authored assets; run with Python 3 (no packages)."""
import json
import struct
from pathlib import Path

root = Path(__file__).resolve().parent
expected = {'personal-core', 'project-engineering', 'project-creative',
            'project-knowledge', 'note-crystal', 'memory-core', 'skill-station',
            'mcp-ship', 'skill-module', 'mcp-vessel'}

def verify():
    assert (root / 'manifest.json').exists(), 'Authored GLB manifest is missing'
    manifest = json.loads((root / 'manifest.json').read_text())
    assert {a['name'] for a in manifest['assets']} == expected
    for asset in manifest['assets']:
        raw = (root / asset['file']).read_bytes()
        magic, version, length = struct.unpack_from('<4sII', raw)
        assert (magic, version, length) == (b'glTF', 2, len(raw))
        size, kind = struct.unpack_from('<I4s', raw, 12)
        assert kind == b'JSON'
        gltf = json.loads(raw[20:20 + size])
        assert gltf['asset']['version'] == '2.0'
        assert not gltf.get('images'), 'Assets must be self-contained geometry/materials'
        assert not any(b.get('uri') for b in gltf['buffers'])
        triangles = sum(gltf['accessors'][p['indices']]['count'] // 3
                        for m in gltf['meshes'] for p in m['primitives'])
        assert triangles == asset['triangles']
        assert 150 < triangles < 45000, (asset['name'], triangles)
        assert len(raw) == asset['bytes']
        assert len(gltf['materials']) == asset['materials']
        assert asset['drawCalls'] <= 8, asset['name']
        print(f"PASS {asset['name']}: {triangles} triangles, {asset['drawCalls']} draws")
    print('PASS all 10 independently authored GLB assets')

if __name__ == '__main__':
    verify()
