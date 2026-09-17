"""Independent, immutable title assets shared by the authenticated workgroup."""
import json
import re
import shutil
import time
import uuid

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .imaging import decode_data
from .paths import atomic_json


class TitleInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    image: str = Field(max_length=36_000_000)
    original: str | None = Field(default=None, max_length=36_000_000)
    parent: str | None = None
    source_job: str | None = None


def register_titles(app, data, member, get_job):
    root = data / 'titles'
    root.mkdir(exist_ok=True)

    def entry(tid):
        if not re.fullmatch(r'[a-f0-9]{32}', tid):
            raise HTTPException(404, '标题不存在')
        path = root / tid / 'metadata.json'
        if not path.is_file():
            raise HTTPException(404, '标题不存在')
        return json.loads(path.read_text(encoding='utf-8'))

    @app.get('/api/titles')
    def titles(limit: int = 100, offset: int = 0, user=Depends(member)):
        if not 1 <= limit <= 200 or offset < 0:
            raise HTTPException(422, '分页参数不合法')
        rows = [json.loads(p.read_text(encoding='utf-8')) for p in root.glob('*/metadata.json')]
        rows.sort(key=lambda row: row['created'], reverse=True)
        return {'items': rows[offset:offset+limit], 'total': len(rows)}

    @app.post('/api/titles')
    def save_title(body: TitleInput, user=Depends(member)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, '请输入标题名称')
        if body.parent:
            entry(body.parent)
        if body.source_job:
            get_job(body.source_job)
        try:
            image = decode_data(body.image).convert('RGBA')
            if not image.getchannel('A').getbbox():
                raise ValueError('标题不能全透明')
            original = decode_data(body.original).convert('RGBA') if body.original else image.copy()
            if original.size != image.size:
                raise ValueError('原始标题与编辑图层尺寸必须相同')
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else '无法读取标题图片')
        tid = uuid.uuid4().hex
        folder = root / tid
        folder.mkdir()
        meta = {'id': tid, 'name': name, 'parent': body.parent, 'source_job': body.source_job,
                'owner': user['name'], 'created': time.time(), 'width': image.width, 'height': image.height}
        try:
            image.save(folder / 'logo.png')
            original.save(folder / 'original.png')
            image.getchannel('A').save(folder / 'alpha.png')
            atomic_json(folder / 'metadata.json', meta)
        except Exception:
            shutil.rmtree(folder)
            raise
        return meta

    @app.get('/api/titles/{tid}')
    def detail(tid: str, user=Depends(member)):
        return entry(tid)

    @app.get('/api/titles/{tid}/{filename}')
    def image_file(tid: str, filename: str, user=Depends(member)):
        entry(tid)
        if filename not in ('logo.png', 'original.png', 'alpha.png'):
            raise HTTPException(404)
        return FileResponse(root / tid / filename, filename=filename)
