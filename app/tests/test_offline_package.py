"""Exercise the real package writer and installer with small offline fixtures."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


def digest(data):return hashlib.sha256(data).hexdigest()


def pack(root,profile):
    folder=root/'offline-ai'/profile;folder.mkdir(parents=True)
    archive=folder/'runtime.zip'
    payload=b'fixture runtime, not executable'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('Lib/site-packages/fixture.txt',payload)
    data=archive.read_bytes();archive.unlink()
    part=folder/f'PosterAI-{profile}.zip.001';part.write_bytes(data)
    manifest={'profile':profile,'import_test_passed':True,'archive_sha256':digest(data),
        'parts':[{'name':part.name,'size':len(data),'sha256':digest(data)}],
        'files':{'Lib/site-packages/fixture.txt':{'size':len(payload),'sha256':digest(payload)}}}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    (folder/'requirements.lock').write_text('fixture==1\n')
    return part


def test_full_package_roundtrip(tmp_path):
    scripts=tmp_path/'app/scripts';scripts.mkdir(parents=True)
    shutil.copy(SCRIPTS/'package_full.py',scripts)
    dist=tmp_path/'dist';dist.mkdir()
    prefix='PosterNormalizer_V0.4.0-rc1/'
    with zipfile.ZipFile(dist/'PosterNormalizer_V0.4.0-rc1_Windows_Offline.zip','w') as z:
        z.writestr(prefix+'start.cmd',b'@echo fixture')
    for profile in ('modern','powerpaint','anytext'):pack(tmp_path,profile)
    subprocess.run([sys.executable,str(scripts/'package_full.py')],check=True,capture_output=True)
    out=tmp_path/'dist-full';manifest=json.loads((out/'full-manifest.json').read_text())
    joined=tmp_path/'joined.zip'
    data=b''.join((out/p['name']).read_bytes() for p in manifest['parts'])
    assert digest(data)==manifest['sha256'];joined.write_bytes(data)
    with zipfile.ZipFile(joined) as z:
        assert z.testzip() is None
        assert z.read(prefix+'start.cmd')==b'@echo fixture'
        assert len([n for n in z.namelist() if n.endswith('manifest.json')])==3


def test_installer_integrity_and_no_replacement_on_corruption(tmp_path):
    spec=importlib.util.spec_from_file_location('test_ai_installer',SCRIPTS/'install_ai.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    installer.ROOT=tmp_path
    part=pack(tmp_path,'modern');installer.install('modern')
    installed=tmp_path/'runtime-ai/modern/Lib/site-packages/fixture.txt'
    previous=installed.read_bytes()
    # Force an upgrade fingerprint without weakening part verification.
    manifest=part.parent/'manifest.json'
    manifest.write_text(manifest.read_text()+'\n')
    part.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='damaged'):installer.install('modern')
    assert installed.read_bytes()==previous
    assert not list((tmp_path/'runtime-ai').glob('install-*'))
    with pytest.raises(ValueError,match='Unsafe'):installer.safe(tmp_path,'../escape')
