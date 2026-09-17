"""Maintainer build step: pin current upstream file revisions and content digests."""
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.model_center import ModelCenter
from app.store import Store
from app.paths import APP_ROOT


def main():
    output=APP_ROOT/'config/model_locks';output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);data=root/'state';data.mkdir()
        center=ModelCenter(data,Store(data/'db.sqlite'),root=root/'models')
        for mid in center.catalog:
            print('Locking',mid,flush=True)
            plan=center.plan(mid,resolve=True)
            (output/f'{mid}.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
            print(mid,len(plan['files']),sum(f.get('size',0) for f in plan['files']),flush=True)


if __name__=='__main__':main()
