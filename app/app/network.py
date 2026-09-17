"""Bounded HTTPS requests. No arbitrary browser-supplied inference endpoints."""
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse
import json
import httpx

MODEL_HOSTS = ('huggingface.co', 'hf.co', 'modelscope.cn', 'modelscope.ai', 'aliyuncs.com',
               'raw.githubusercontent.com', 'download.pytorch.org')
ASSET_HOSTS = ('api.themoviedb.org', 'image.tmdb.org', 'api.tvmaze.com', 'static.tvmaze.com',
               'webservice.fanart.tv', 'assets.fanart.tv')


def checked_url(url, hosts):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('仅允许受信 HTTPS 来源')
    if not any(host == h or host.endswith('.'+h) for h in hosts):
        raise ValueError('下载跳转到了未允许的来源域名')
    return url


@contextmanager
def stream(url, headers=None, hosts=MODEL_HOSTS):
    with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False) as client:
        for _ in range(8):
            checked_url(url, hosts)
            with client.stream('GET', url, headers=headers or {}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                response.raise_for_status()
                yield response
                return
        raise ValueError('重定向次数过多')


def fetch_json(url, headers=None, hosts=MODEL_HOSTS):
    with stream(url, headers, hosts) as response:
        data = bytearray()
        for block in response.iter_bytes(65536):
            data.extend(block)
            if len(data) > 16*1024*1024:
                raise ValueError('元数据响应过大')
        return json.loads(data)
