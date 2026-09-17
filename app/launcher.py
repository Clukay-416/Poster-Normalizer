import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)


def main():
    import uvicorn
    from app.main import create_app
    from app.paths import default_data
    data=default_data();data.mkdir(parents=True,exist_ok=True)
    lock=(data/'server.lock').open('a+b')
    try:
        if lock.seek(0,2)==0:
            lock.write(b'0');lock.flush()
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        print('This installation is already running. Close the other window first.')
        return 1
    config = json.loads((ROOT/'config/default.json').read_text(encoding='utf-8'))
    port = int(os.environ.get('POSTER_PORT', config['port']))
    # Refuse any occupied port; do not accidentally launch another worker.
    probe = socket.socket()
    try:
        probe.bind(('0.0.0.0', port))
    except OSError:
        print(f'Port {port} is already in use. Close the other window, or set POSTER_PORT.')
        return 1
    finally:
        probe.close()
    app = create_app()
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(),logging.FileHandler(data/'server.log',encoding='utf-8')])
    print('\nPoster Normalizer V0.3.1 / Offline application + separate model depot')
    print(f'Local: http://127.0.0.1:{port}')
    try:
        for ip in sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)}):
            if not ip.startswith('127.'):
                print(f'LAN:   http://{ip}:{port}')
    except OSError:
        pass
    print('Admin code: ',app.state.codes['admin'])
    print('Member code:',app.state.codes['member'])
    print('Keep this window open. Ctrl+C stops the server.\n')
    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
                    health = json.load(response)
                    if health.get('application') == 'poster-normalizer':
                        webbrowser.open(f'http://127.0.0.1:{port}')
                        return
            except Exception:
                time.sleep(.5)
    threading.Thread(target=open_when_ready,daemon=True).start()
    uvicorn.run(app, host=config['host'], port=port, workers=1, access_log=False, limit_concurrency=30)
    return 0


if __name__ == '__main__':
    sys.exit(main())
