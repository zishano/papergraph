import httpx
import pytest
from papergraph.retrieval.scholar import ScholarProvider
from papergraph.retrieval.openalex import OpenAlexClient, RetrievalError
from papergraph.graph.crawler import CitationCrawler
from papergraph.models import CrawlConfig
from papergraph.models import Paper


def entry(title, cid):
    return {'title': title, 'result_id': cid, 'inline_links': {'cited_by': {'cites_id': cid}}}


async def test_scholar_bfs_ignores_openalex_edges_and_metadata_failure(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-key')
    calls=[]
    def handler(r):
        if r.url.host == 'api.openalex.org':
            return httpx.Response(503)
        if r.url.host == 'arxiv.org':
            return httpx.Response(200, text='<html><form></form></html>')
        calls.append(dict(r.url.params))
        if r.url.params.get('q'):
            return httpx.Response(200,json={'organic_results':[entry('Seed','1')]})
        rows = [entry('A','2'),entry('B','3')] if r.url.params['cites']=='1' else [entry('C','4')]
        return httpx.Response(200,json={'organic_results':rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p=ScholarProvider(http,OpenAlexClient(http),backend="serpapi")
        seed=await p.resolve('Seed')
        g=await CitationCrawler(p).crawl([seed],CrawlConfig(depth=2))
        assert len(g.nodes)==4
        assert len([x for x in g.paths if x.paper_id=='scholar:4'])==2
        assert p.warnings
        assert len(calls)==4


async def test_csv_keeps_unknown_members(monkeypatch,tmp_path):
    monkeypatch.delenv('SERPAPI_API_KEY',raising=False)
    f=tmp_path/'list.csv';f.write_text('title,paper_id\nUnknown,\nUnknown,\n')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(404))) as http:
        p=ScholarProvider(http,OpenAlexClient(http),str(f))
        seed=await p.resolve('Seed')
        members,limited,_=await p.neighbors(seed,'citing',10)
        assert len(members)==1 and members[0].openalex_id is None
        assert not limited


def test_no_silent_fallback(monkeypatch):
    monkeypatch.delenv('SERPAPI_API_KEY',raising=False)
    with pytest.raises(ValueError,match='no OpenAlex fallback'):
        ScholarProvider(None,None,backend="serpapi")


async def test_error_redacts_api_key(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY','secret-test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403,text='secret-test'))) as http:
        with pytest.raises(RetrievalError) as exc:
            await ScholarProvider(http,OpenAlexClient(http),backend="serpapi").resolve('Seed')
        assert 'secret-test' not in str(exc.value)


async def test_pagination_and_request_cache(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY','test-key')
    starts=[]
    def handler(r):
        if r.url.host=='api.openalex.org':
            return httpx.Response(200,json={'results':[]})
        if r.url.host=='arxiv.org':
            return httpx.Response(200,text='<html><form></form></html>')
        start=int(r.url.params.get('start',0));starts.append(start)
        return httpx.Response(200,json={'organic_results':[entry('A','2')] if start==0 else [entry('A','2'),entry('B','3')],
            'pagination':{'next':'unused-url'} if start==0 else {}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p=ScholarProvider(http,OpenAlexClient(http),backend="serpapi")
        seed=await p.paper(entry('Seed','1'))
        rows,limited,_=await p.neighbors(seed,'citing',10)
        assert len(rows)==2 and not limited
        await p.neighbors(seed,'citing',10)
        assert starts==[0,20]


async def test_truncated_scholar_abstract_is_enriched_from_arxiv(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-key')
    title = 'ThermoDSE: A Thermal-Aware Design Space Exploration'
    html = f'''<li class="arxiv-result"><p class="title">{title}</p>
        <span class="abstract-full">This is the complete abstract with all conclusions. △ Less</span></li>'''
    class EmptyOpenAlex:
        async def search_title(self, value):
            return []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=html))) as http:
        provider = ScholarProvider(http, EmptyOpenAlex(), backend='serpapi')
        paper = await provider.paper({'title': title, 'result_id': '1',
                                      'snippet': 'This is truncated …'})
    assert paper.abstract == 'This is the complete abstract with all conclusions.'
    assert any('Abstract enriched via arxiv' in warning for warning in provider.warnings)


async def test_truncated_scholar_abstract_is_enriched_by_doi_fallback(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-key')
    title = 'A DOI Paper'
    class DoiOpenAlex:
        async def search_title(self, value):
            return [Paper(id='W1', openalex_id='W1', doi='10.1000/example', title=title,
                          abstract='Truncated …')]
    def handler(request):
        if request.url.host == 'arxiv.org':
            return httpx.Response(200, text='<html><form></form></html>')
        return httpx.Response(200, json={'title': title, 'abstract': 'Complete DOI abstract.'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = ScholarProvider(http, DoiOpenAlex(), backend='serpapi')
        paper = await provider.paper({'title': title, 'result_id': '1', 'snippet': 'Snippet …'})
    assert paper.abstract == 'Complete DOI abstract.'
    assert any('semantic_scholar_doi' in warning for warning in provider.warnings)
