"""Build time only. Vendor pinned upstream inference code with original licenses."""
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    sources=json.loads((ROOT/'config/ai_sources.json').read_text())
    output=ROOT/'ai_vendor';output.mkdir(exist_ok=True)
    for name,source in sources.items():
        url=f"https://codeload.github.com/{source['repo']}/tar.gz/{source['revision']}"
        print('Fetching pinned source',name,flush=True)
        raw=urllib.request.urlopen(url,timeout=120).read()
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            for entry in archive:
                path='/'.join(entry.name.split('/')[1:])
                if not entry.isfile() or not path:continue
                if '..' in Path(path).parts or Path(path).is_absolute():raise ValueError('Unsafe source archive')
                if not (path in source['files'] or any(path.startswith(p) for p in source['prefixes'])):continue
                if Path(path).suffix not in ('.py','.yaml','.yml','.txt','.md') and not Path(path).name.startswith('LICENSE'):continue
                dest=output/name/path;dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(archive.extractfile(entry).read())
    # Explicit OFL font, avoiding unlicensed system fonts from upstream example bundles.
    font=output/'fonts';font.mkdir(exist_ok=True)
    font_manifest=json.loads((ROOT/'config/font_source.json').read_text())
    for name,item in font_manifest.items():
        raw=urllib.request.urlopen(item['url'],timeout=120).read()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Font checksum mismatch')
        (font/name).write_bytes(raw)
    files={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in output.rglob('*') if p.is_file() and p.name!='manifest.json'}
    (output/'manifest.json').write_text(json.dumps({'sources':sources,'files':files},indent=2),encoding='utf-8')


if __name__=='__main__':main()
