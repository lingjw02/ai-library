"""Download a pinned public model and verify every file against Hub metadata."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.embedding.service import MODEL_ID, REVISION, DEFAULT_MODEL_DIR


def main():
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info(MODEL_ID, revision=REVISION, files_metadata=True, token=False)
    if info.sha != REVISION:
        raise ValueError('Hub returned an unexpected revision')
    names = [f.rfilename for f in info.siblings if f.rfilename.endswith(('.json', '.safetensors', '.model', '.jinja', '.md'))]
    folder = Path(snapshot_download(MODEL_ID, revision=REVISION, local_dir=DEFAULT_MODEL_DIR,
        allow_patterns=names, token=False, max_workers=3))
    hashes = {}
    for entry in info.siblings:
        if entry.rfilename not in names:
            continue
        path = folder / entry.rfilename
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if entry.lfs:
            if digest != entry.lfs.sha256:
                raise ValueError('LFS SHA256 mismatch: ' + entry.rfilename)
        else:
            body = path.read_bytes()
            git_hash = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
            if git_hash != entry.blob_id:
                raise ValueError('Git blob mismatch: ' + entry.rfilename)
        hashes[entry.rfilename] = digest
    manifest = {'model_id': MODEL_ID, 'revision': REVISION, 'files': hashes,
                'verification': 'official_hub_revision_and_git_blob_or_lfs_hash'}
    pending = folder / 'deployment.pending.json'
    pending.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    pending.replace(folder / 'deployment.json')
    print(json.dumps({'model_dir': str(folder), 'files_verified': len(hashes), 'revision': REVISION}))


if __name__ == '__main__':
    main()
