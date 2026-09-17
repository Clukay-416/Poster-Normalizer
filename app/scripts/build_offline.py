"""Build-time only: collect official runtime binaries and lock all Windows wheels."""
import email
import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.markers import default_environment

ROOT=Path(__file__).resolve().parents[2]
OFFLINE=ROOT/'offline'
BINARIES={
    'python-3.13.15-embed-amd64.zip':'https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip',
    'VC_redist.x64.exe':'https://aka.ms/vs/17/release/vc_redist.x64.exe',
}


def main():
    OFFLINE.mkdir(exist_ok=True)
    for name,url in BINARIES.items():
        path=OFFLINE/name
        if not path.exists():
            print('Fetching',name,flush=True)
            with urllib.request.urlopen(url,timeout=45) as response,path.with_suffix(path.suffix+'.part').open('wb') as out:
                while chunk:=response.read(1024*1024):out.write(chunk)
            path.with_suffix(path.suffix+'.part').replace(path)
    with zipfile.ZipFile(OFFLINE/'python-3.13.15-embed-amd64.zip') as archive:
        assert archive.testzip() is None
        assert 'python.exe' in archive.namelist()
    wheels=list((OFFLINE/'wheels').glob('*.whl'))
    if not wheels:raise RuntimeError('Download Windows wheels before running the builder')
    versions={};metadatas=[]
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            if archive.testzip():raise RuntimeError('Corrupt wheel: '+wheel.name)
            meta=email.message_from_bytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
            versions[canonicalize_name(meta['Name'])]=meta['Version']
            metadatas.append(meta)
    env=default_environment();env.update(python_version='3.13',python_full_version='3.13.15',sys_platform='win32',os_name='nt',platform_system='Windows',platform_machine='AMD64',extra='')
    missing=[]
    for meta in metadatas:
        for line in meta.get_all('Requires-Dist',[]):
            requirement=Requirement(line)
            if requirement.marker and not requirement.marker.evaluate(env):continue
            name=canonicalize_name(requirement.name)
            if name not in versions or (requirement.specifier and versions[name] not in requirement.specifier):
                missing.append(f"{meta['Name']} requires {line}")
    if missing:raise RuntimeError('Incomplete Windows dependency closure: '+str(missing))
    (OFFLINE/'requirements-win313.lock').write_text('\n'.join(f'{n}=={v}' for n,v in sorted(versions.items()))+'\n',encoding='utf-8')
    files={}
    for path in sorted(OFFLINE.rglob('*')):
        if not path.is_file() or path.name=='manifest.json' or path.suffix=='.part':continue
        files[path.relative_to(OFFLINE).as_posix()]={'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest={'python':'3.13.15','platform':'win_amd64','binary_sources':BINARIES,'wheel_source':'https://pypi.org/','files':files,'dependency_closure_verified':True}
    (OFFLINE/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(files),'wheels':len(wheels),'bytes':sum(f['size'] for f in files.values()),'dependency_closure_verified':True}),flush=True)


if __name__=='__main__':main()
