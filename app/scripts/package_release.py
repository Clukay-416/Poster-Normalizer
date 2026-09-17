"""Create a clean portable Windows application ZIP, without data or weights."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[2]
out=root.parent/'PosterNormalizer_V0.3.0_Windows_Offline.zip'
manifest=json.loads((root/'offline/manifest.json').read_text())
for name,entry in manifest['files'].items():
    path=root/'offline'/name
    assert path.stat().st_size==entry['size'],name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],name
assert (root/'offline/VC_redist.x64.exe').read_bytes()[:2]==b'MZ'
excluded={'__pycache__','.pytest_cache','.git','.venv','runtime','data','models'}
paths=[]
for p in sorted(root.rglob('*')):
    rel=p.relative_to(root)
    if not p.is_file() or any(part in excluded for part in rel.parts):continue
    if p.suffix in ('.pyc','.part','.log','.db','.sqlite','.sqlite3','.onnx','.pt','.pth','.safetensors'):continue
    paths.append((p,rel))
prefix='PosterNormalizer_V0.3.0/'
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p,rel in paths:
        blob=p.read_bytes()
        if p.suffix.lower()=='.cmd':blob=blob.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        z.writestr(prefix+rel.as_posix(),blob)
with zipfile.ZipFile(out) as z:
    assert z.testzip() is None
    assert prefix+'start.cmd' in z.namelist()
    for name in manifest['files']:
        assert hashlib.sha256(z.read(prefix+'offline/'+name)).hexdigest()==manifest['files'][name]['sha256']
sha=hashlib.sha256(out.read_bytes()).hexdigest()
out.with_suffix('.sha256.txt').write_text(sha+'  '+out.name+'\n')
print(json.dumps({'path':str(out),'bytes':out.stat().st_size,'files':len(paths),'sha256':sha,'models_included':False,'archive_verified':True}))
