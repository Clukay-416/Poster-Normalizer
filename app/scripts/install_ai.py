"""Offline runtime installer. Verify every split part and extracted file before activation."""
import hashlib
import json
import os
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def safe(root,name):
    if '\\' in name or ':' in name or name.startswith('/') or '..' in Path(name).parts:raise ValueError('Unsafe archive path')
    path=(root/name).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Unsafe archive path')
    return path


def install(profile):
    source=ROOT/'offline-ai'/profile
    manifest_path=source/'manifest.json'
    if not manifest_path.is_file():
        source=ROOT/'offline-ai'
        manifest_path=source/f'PosterAI-{profile}.manifest.json'
    manifest=json.loads(manifest_path.read_text())
    if manifest['profile']!=profile or not manifest.get('import_test_passed'):raise ValueError('Runtime manifest invalid')
    base=ROOT/'runtime-ai';base.mkdir(exist_ok=True)
    target=base/profile
    fingerprint=sha(manifest_path)
    if (target/'installed.txt').is_file() and (target/'installed.txt').read_text().strip()==fingerprint:
        print(profile,'already installed');return
    needed=sum(v['size'] for v in manifest['files'].values())+sum(v['size'] for v in manifest['parts'])
    if shutil.disk_usage(base).free<needed:raise ValueError('Not enough free disk space')
    stage=base/('install-'+uuid.uuid4().hex);stage.mkdir()
    joined=stage/'runtime.zip'
    try:
        with joined.open('wb') as out:
            for item in manifest['parts']:
                part=safe(source,item['name'])
                if not part.is_file() or part.stat().st_size!=item['size'] or sha(part)!=item['sha256']:
                    raise ValueError('Missing or damaged runtime part: '+item['name'])
                with part.open('rb') as f:shutil.copyfileobj(f,out,8*1024*1024)
        if sha(joined)!=manifest['archive_sha256']:raise ValueError('Runtime archive checksum mismatch')
        unpack=stage/'unpack';unpack.mkdir()
        with zipfile.ZipFile(joined) as archive:
            if set(archive.namelist())!=set(manifest['files']):raise ValueError('Runtime file list mismatch')
            for name,item in manifest['files'].items():
                dest=safe(unpack,name);dest.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(name) as src,dest.open('wb') as out:shutil.copyfileobj(src,out,8*1024*1024)
                if dest.stat().st_size!=item['size'] or sha(dest)!=item['sha256']:raise ValueError('Corrupt runtime file: '+name)
        (unpack/'installed.txt').write_text(fingerprint)
        backup=base/(profile+'-backup-'+uuid.uuid4().hex)
        if target.exists():target.rename(backup)
        try:unpack.rename(target)
        except OSError:
            if backup.exists():backup.rename(target)
            raise
        if backup.exists():shutil.rmtree(backup,ignore_errors=True)
        print(profile,'installed without network access')
    finally:
        shutil.rmtree(stage,ignore_errors=True)


if __name__=='__main__':
    # The operator must shut down the application before replacing runtimes.
    lock=ROOT/'data/server.lock'
    if os.name=='nt' and lock.exists():
        import msvcrt
        with lock.open('r+b') as f:
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise SystemExit('Close Poster Normalizer before installing AI runtimes.')
    found=False
    for profile in ('modern','powerpaint','anytext'):
        if (ROOT/'offline-ai'/profile/'manifest.json').is_file() or (ROOT/'offline-ai'/f'PosterAI-{profile}.manifest.json').is_file():
            found=True;install(profile)
    if not found:raise SystemExit('No offline AI packs. Place downloaded profile folders inside offline-ai first.')
