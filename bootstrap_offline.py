"""Run only with the packaged embedded interpreter, never contacts an index."""
import ctypes
import os
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parent
wheelhouse=root/'offline/wheels'
if sys.version_info[:2]!=(3,13):
    raise SystemExit('This wheelhouse is specifically for CPython 3.13 x64.')
try:
    ctypes.WinDLL('msvcp140.dll')
except OSError:
    print('Microsoft Visual C++ runtime is required. Opening the bundled official installer.',flush=True)
    result=subprocess.run([str(root/'offline/VC_redist.x64.exe'),'/install','/passive','/norestart'])
    if result.returncode not in (0,1638,3010):
        raise SystemExit('Visual C++ installation was not completed; please allow its UAC prompt or contact your administrator.')
sys.path.insert(0,str(next(wheelhouse.glob('pip-*.whl'))))
from pip._internal.cli.main import main
code=main(['install','--no-index','--no-deps','--disable-pip-version-check','--no-warn-script-location',
           '--find-links',str(wheelhouse),'--target',str(Path(sys.executable).parent/'Lib/site-packages'),
           '-r',str(root/'offline/requirements-win313.lock')])
if code:raise SystemExit(code)
check='import fastapi,uvicorn,multipart,PIL,cv2,numpy,psutil,httpx,onnxruntime; print("Offline dependencies OK")'
raise SystemExit(subprocess.call([sys.executable,'-c',check]))
