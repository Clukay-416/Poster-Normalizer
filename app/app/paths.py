"""Application, state, runtime and movable model storage have separate roots."""
import json
import os
import uuid
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
INSTALL_ROOT = Path(os.getenv('POSTER_INSTALL_ROOT', APP_ROOT.parent)).resolve()


def default_data():
    return Path(os.getenv('POSTER_DATA', INSTALL_ROOT / 'data')).resolve()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def child(root, name):
    """Reject traversal, symlink escape and Windows alternate streams."""
    if not name or '\\' in name or ':' in name or name.startswith('/'):
        raise ValueError('不合法的相对文件路径')
    parts = Path(name).parts
    if '..' in parts or '.' in parts:
        raise ValueError('不允许路径穿越')
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('路径越出目录')
    return path
