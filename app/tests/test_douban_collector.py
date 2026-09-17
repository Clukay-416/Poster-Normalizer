import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('douban_candidates',Path(__file__).parents[1]/'scripts/douban_candidates.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_candidates_are_bounded_and_deduplicated():
    result=module.parse_html('<title>Example</title><img src="https://img1.doubanio.com/view/photo/s_ratio_poster/public/a.jpg"><img src="https://evil.example/photo/x.jpg"><img src="https://img1.doubanio.com/view/photo/s_ratio_poster/public/a.jpg">','https://movie.douban.com/subject/123/photos?type=R')
    assert result['title']=='Example'
    assert len(result['images'])==1
    assert result['rights_verified'] is False


@pytest.mark.parametrize('url',['http://movie.douban.com/subject/1/','https://movie.douban.com.evil.test/subject/1/','https://movie.douban.com/subject/1/?next=evil','https://movie.douban.com/people/1/'])
def test_url_scope(url):
    with pytest.raises(ValueError):module.validate_url(url)


def test_challenge_stops():
    with pytest.raises(ValueError,match='安全验证'):
        module.parse_html('<title>安全验证</title>','https://movie.douban.com/subject/1/')
