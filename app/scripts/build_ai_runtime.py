"""Windows CI: resolve wheels, build a movable embedded runtime and split offline ZIP."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PYTHON='3.10.11'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def build(profile):
    if os.name!='nt' or sys.version_info[:2]!=(3,10):raise RuntimeError('Build with Windows CPython 3.10')
    out=ROOT/'offline-ai'/profile;out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='poster-runtime-') as tmp:
        temp=Path(tmp);wheels=temp/'wheels';wheels.mkdir()
        index='https://download.pytorch.org/whl/'+('cu118' if profile=='anytext' else 'cu124')
        subprocess.run([sys.executable,'-m','pip','wheel','--prefer-binary','--extra-index-url',index,
            '-r',str(ROOT/f'offline/ai-{profile}.in'),'-w',str(wheels)],check=True)
        runtime=temp/'runtime';runtime.mkdir()
        url=f'https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip'
        archive=temp/'python.zip';urllib.request.urlretrieve(url,archive)
        with zipfile.ZipFile(archive) as z:z.extractall(runtime)
        (runtime/'python310._pth').write_text('python310.zip\n.\nLib/site-packages\nimport site\n')
        site=runtime/'Lib/site-packages'
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--target',str(site),
            *[str(p) for p in wheels.glob('*.whl')]],check=True)
        env={**os.environ,'PYTHONNOUSERSITE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
        subprocess.run([str(runtime/'python.exe'),str(ROOT/'app/scripts/check_ai_imports.py'),profile],env=env,check=True)
        freeze=subprocess.check_output([str(runtime/'python.exe'),'-m','pip','list','--format=json'],env=env,text=True)
        (runtime/'packages.json').write_text(freeze)
        lock='\n'.join(f"{p['name']}=={p['version']}" for p in json.loads(freeze))+'\n'
        (out/'requirements.lock').write_text(lock)
        package=temp/'runtime.zip'
        files={}
        with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            for path in sorted(runtime.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts:continue
                name=path.relative_to(runtime).as_posix();z.write(path,name)
                files[name]={'size':path.stat().st_size,'sha256':sha(path)}
        parts=[]
        with package.open('rb') as stream:
            number=1
            while True:
                blob=stream.read(900*1024*1024)
                if not blob:break
                name=f'PosterAI-{profile}.zip.{number:03d}';path=out/name;path.write_bytes(blob)
                parts.append({'name':name,'size':len(blob),'sha256':sha(path)});number+=1
        (out/'manifest.json').write_text(json.dumps({'schema':1,'profile':profile,'python':PYTHON,
            'python_source':url,'torch_index':index,'parts':parts,'files':files,'archive_sha256':sha(package),
            'import_test_passed':True,'gpu_inference_test_passed':False},indent=2))
        print(json.dumps({'profile':profile,'compressed_bytes':package.stat().st_size,'parts':len(parts)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('profile',choices=['modern','powerpaint','anytext'])
    build(parser.parse_args().profile)
