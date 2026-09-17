"""Single-page candidate collector; never bypasses login, robots or challenges."""
import argparse
import hashlib
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

UA='PosterNormalizer/0.3 (single-page metadata collector)'
LIMIT=4*1024*1024


def validate_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.netloc!='movie.douban.com' or p.fragment:
        raise ValueError('仅接受 movie.douban.com 的 HTTPS 条目链接')
    if not re.fullmatch(r'/subject/\d+/(?:photos/?)?',p.path):
        raise ValueError('仅支持单个电影条目或其 photos 页面')
    if p.query and p.query not in ('type=R','type=S'):
        raise ValueError('仅支持 type=R（海报）或 type=S（剧照）')
    return url


class Candidates(HTMLParser):
    def __init__(self):
        super().__init__();self.images=[];self.title=[];self.in_title=False

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='title':self.in_title=True
        values=[]
        if tag=='img':values=[a.get('src',''),a.get('data-src',''),a.get('data-original','')]
        if tag=='meta' and a.get('property')=='og:image':values=[a.get('content','')]
        for value in values:
            if value.startswith('//'):value='https:'+value
            p=urlparse(value);host=p.hostname or ''
            if p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and (host=='doubanio.com' or host.endswith('.doubanio.com')):
                if '/photo/' in p.path and value not in self.images:self.images.append(value)

    def handle_endtag(self,tag):
        if tag=='title':self.in_title=False

    def handle_data(self,data):
        if self.in_title:self.title.append(data)


def parse_html(html,url):
    if any(s in html.lower() for s in ('sec.douban.com','captcha','异常请求','安全验证')):
        raise ValueError('页面包含安全验证，已停止；请使用有权使用的本地素材')
    parser=Candidates();parser.feed(html)
    return {'source':validate_url(url),'title':''.join(parser.title).strip(),
            'images':parser.images,'rights_verified':False,'note':'候选链接；未保证原图尺寸、透明标题或素材授权'}


def collect(url,cache):
    import httpx
    validate_url(url);cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(url.encode()).hexdigest();saved=cache/(key+'.html')
    if saved.exists() and time.time()-saved.stat().st_mtime<86400:
        return parse_html(saved.read_text(encoding='utf-8'),url)
    with httpx.Client(headers={'User-Agent':UA},timeout=20,follow_redirects=False) as client:
        def fetch(target):
            with client.stream('GET',target) as response:
                if response.status_code!=200:
                    raise ValueError(f'来源响应 {response.status_code}；停止请求，不跟随登录/验证跳转')
                chunks=bytearray()
                for block in response.iter_bytes(65536):
                    chunks.extend(block)
                    if len(chunks)>LIMIT:raise ValueError('页面超过大小上限')
                return bytes(chunks).decode('utf-8',errors='replace')
        robots=fetch('https://movie.douban.com/robots.txt')
        if 'user-agent:' not in robots.lower():raise ValueError('无法核实 robots 规则，停止抓取')
        policy=RobotFileParser();policy.parse(robots.splitlines())
        if not policy.can_fetch(UA,url):raise ValueError('robots 不允许此页面采集，停止抓取')
        delay=policy.crawl_delay(UA) or 0
        if delay>60:raise ValueError('来源要求较长等待，本次停止，请使用本地素材')
        time.sleep(max(2,delay))
        html=fetch(url);result=parse_html(html,url)
        saved.write_text(html,encoding='utf-8')
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url');parser.add_argument('--html',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();validate_url(args.url)
    if args.html:
        if args.html.stat().st_size>LIMIT:raise ValueError('本地 HTML 超过大小上限')
        result=parse_html(args.html.read_text(encoding='utf-8'),args.url)
        result['input']='local_html'
    else:result=collect(args.url,Path(__file__).resolve().parents[2]/'data/douban-cache')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"已保存 {len(result['images'])} 个候选；尚未下载图片。")


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit(str(exc) if isinstance(exc,ValueError) else type(exc).__name__+'：采集失败，未继续请求')
