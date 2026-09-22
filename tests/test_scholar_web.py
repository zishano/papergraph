import httpx
import pytest
from papergraph.retrieval.scholar_web import parse_scholar_html
from papergraph.retrieval.scholar import ScholarProvider
from papergraph.retrieval.openalex import RetrievalError
from papergraph.cli import parser

HTML = '''<div id="gs_res_ccl_mid"><div class="gs_r gs_or gs_scl" data-cid="ABC">
<h3 class="gs_rt"><span class="gs_ct1">[PDF]</span><a href="https://example.com/paper">A Paper</a></h3>
<div class="gs_fl"><a href="/scholar?cites=123&amp;hl=en">Cited by 3</a></div></div></div>
<div id="gs_n"><a href="/scholar?cites=123&amp;start=10"><span class="gs_ico_nav_next"></span></a></div>'''


def test_parse_title_citation_id_and_pagination():
    data=parse_scholar_html(HTML)
    assert data['organic_results'][0]['title']=='A Paper'
    assert data['organic_results'][0]['inline_links']['cited_by']['cites_id']=='123'
    assert data['next_start']==10


@pytest.mark.parametrize('html',['<p>unusual traffic</p>', '<div id="captcha-form"></div>', '<html>login</html>', '<div id="gs_res_ccl_mid"></div>'])
def test_blocked_and_unknown_are_errors(html):
    with pytest.raises(RetrievalError):
        parse_scholar_html(html)


def test_explicit_empty_results():
    assert parse_scholar_html('<div id="gs_res_ccl_mid">Your search did not match any articles</div>')['organic_results']==[]


async def test_default_web_without_key_and_cache(monkeypatch):
    monkeypatch.delenv('SERPAPI_API_KEY',raising=False)
    calls=[]
    def handler(r):
        calls.append(r)
        assert r.url.host=='scholar.google.com'
        assert 'api_key' not in r.url.params
        return httpx.Response(200,text=HTML)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p=ScholarProvider(http,None)
        await p.query(q='A Paper',num=20)
        await p.query(q='A Paper',num=20)
        assert len(calls)==1
    args = parser().parse_args(['search','--seed','A Paper'])
    assert args.source == 'openalex'
    assert args.direction == 'citing'
    assert args.scholar_backend == 'web'


async def test_http_block_is_not_empty():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(429))) as http:
        with pytest.raises(RetrievalError,match='restricted'):
            await ScholarProvider(http,None).query(q='A Paper')


async def test_proxy_cli_forwarding(monkeypatch):
    from papergraph.cli import run
    seen={}
    class StopClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        async def __aenter__(self):
            raise RuntimeError('test stop before network')
        async def __aexit__(self,*args):
            pass
    monkeypatch.setattr('papergraph.cli.httpx.AsyncClient',StopClient)
    monkeypatch.setenv('PAPERGRAPH_PROXY','http://localhost:8888')
    args=parser().parse_args(['search','--seed','A Paper','--proxy','http://127.0.0.1:7890'])
    with pytest.raises(RuntimeError):
        await run(args)
    assert seen['proxy']=='http://127.0.0.1:7890'
    args.proxy=None
    with pytest.raises(RuntimeError):
        await run(args)
    assert seen['proxy']=='http://localhost:8888'


async def test_network_error_does_not_expose_proxy_credentials():
    def handler(request):
        raise httpx.ProxyError('http://user:secret@localhost:7890')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RetrievalError,match='proxy connection failed') as exc:
            await ScholarProvider(http,None).query(q='A Paper')
        assert 'secret' not in str(exc.value)
