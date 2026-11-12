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


async def test_snippet_without_ellipsis_is_not_a_full_abstract(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-key')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        provider = ScholarProvider(http, OpenAlexClient(http), backend='serpapi')
        paper = await provider.paper({'title': 'A Paper', 'result_id': '1',
                                     'snippet': 'A short search excerpt without ellipsis.'})
    assert paper.abstract_status == 'snippet'
    assert paper.abstract_source == 'google_scholar_snippet'
    assert any('Full abstract unavailable' in w for w in provider.warnings)


async def test_landing_metadata_replaces_snippet_and_fills_authors(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test-key')
    def handler(request):
        if request.url.host == 'arxiv.org':
            assert request.url.path == '/abs/2608.12345'
            return httpx.Response(200, text='''<meta name="citation_title" content="A Paper">
                <meta name="citation_author" content="A Researcher">
                <meta name="citation_date" content="2026/08/01">
                <blockquote class="abstract">Abstract: Full abstract.</blockquote>''')
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = ScholarProvider(http, OpenAlexClient(http), backend='serpapi')
        paper = await provider.paper({'title': 'A Paper', 'result_id': '1',
                                     'snippet': 'A much longer incomplete search excerpt …',
                                     'link': 'https://arxiv.org/pdf/2608.12345'})
    assert paper.abstract == 'Full abstract.'
    assert paper.abstract_status == 'complete'
    assert paper.authors == ['A Researcher']
    assert paper.year == 2026


async def test_incomplete_abstract_never_sent_to_gemini():
    from papergraph.gemini import GeminiScorer
    from papergraph.models import CitationGraph, PaperNode
    graph = CitationGraph(config=CrawlConfig(), seed_ids=['seed'], nodes=[
        PaperNode(id='snippet', openalex_id=None, title='A', abstract='Excerpt',
                  abstract_status='snippet', hop=1, seed_ids=['seed'], parent_ids=['seed'])
    ], edges=[], paths=[], seed_relations=[])
    class NoNetwork:
        async def post(self, *args, **kwargs):
            pytest.fail('Incomplete abstracts must not be scored')
    result = await GeminiScorer(NoNetwork(), api_key='test').analyze(graph, '')
    assert result[0]['status'] == 'skipped_incomplete_abstract'
    assert result[0]['score'] is None


async def test_pdf_abstract_extraction_checks_identity_and_boundaries(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('papergraph.retrieval.scholar.shutil.which', lambda _: '/usr/bin/pdftotext')
    abstract = 'This is a complete abstract describing a simulator and its evaluation in sufficient detail to test extraction.'
    monkeypatch.setattr('papergraph.retrieval.scholar.subprocess.run',
                        lambda *a, **kw: SimpleNamespace(stdout=f'A Paper\nAbstract\n{abstract}\n1 Introduction\nBody'.encode()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b'%PDF-1.7 mock'))) as http:
        provider = ScholarProvider(http, None)
        assert await provider._pdf_abstract('https://example.org/paper.pdf', 'A Paper') == abstract
        assert await provider._pdf_abstract('https://example.org/paper.pdf', 'Another Title') is None


async def test_openalex_identifier_lookup_precedes_title_and_preserves_scholar_edges(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test')
    calls = []
    class Metadata:
        async def get_work(self, identifier):
            calls.append(identifier)
            return Paper(id='W1', openalex_id='W1', title='A Paper',
                         abstract='Full abstract.', referenced_works=['W999'])
        async def search_title(self, title):
            pytest.fail('Identifier match should not require a title search')
    async with httpx.AsyncClient() as http:
        provider = ScholarProvider(http, Metadata(), backend='serpapi')
        paper = await provider.paper({'title': 'A Paper', 'result_id': 'result',
                                     'link': 'https://arxiv.org/pdf/2608.12345'})
    assert calls == ['10.48550/arxiv.2608.12345']
    assert paper.id == 'scholar:result' and paper.openalex_id == 'W1'
    assert paper.referenced_works == []
    assert paper.abstract_status == 'complete'


async def test_second_hop_recovers_cites_link_and_uses_scholar_only(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test')
    class Metadata:
        async def search_title(self, title):
            return [Paper(id='W' + title, openalex_id='W' + title, title=title,
                          abstract='Full abstract.', referenced_works=['unrelated'])]
    def handler(request):
        params = request.url.params
        if params.get('q') == 'Seed':
            rows = [entry('Seed', '1')]
        elif params.get('q') == '"A"':
            rows = [{'title': 'A', 'result_id': 'a-version',
                     'inline_links': {'cited_by': {'link': 'https://scholar.google.com/scholar?cites=2'}}}]
        elif params.get('cites') == '1':
            rows = [{'title': 'A', 'result_id': 'a'}]
        elif params.get('cites') == '2':
            rows = [entry('B', '3')]
        else:
            pytest.fail('Unexpected discovery request')
        return httpx.Response(200, json={'organic_results': rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = ScholarProvider(http, Metadata(), backend='serpapi')
        seed = await provider.resolve('Seed')
        graph = await CitationCrawler(provider).crawl([seed], CrawlConfig(depth=2))
    assert {(n.title, n.hop) for n in graph.nodes} == {('Seed', 0), ('A', 1), ('B', 2)}
    assert len(graph.edges) == 2 and not graph.truncated
    assert all(n.abstract_source == 'openalex' for n in graph.nodes)


async def test_scholar_retry_finds_alternate_full_abstract(monkeypatch):
    monkeypatch.setenv('SERPAPI_API_KEY', 'test')
    class EmptyMetadata:
        async def search_title(self, title):
            return []
    def handler(request):
        if request.url.host == 'serpapi.com':
            return httpx.Response(200, json={'organic_results': [
                {'title': 'Other Paper', 'link': 'https://wrong.example/paper'},
                {'title': 'A Paper', 'link': 'https://alternate.example/paper'}]})
        if request.url.host == 'alternate.example':
            return httpx.Response(200, text='<meta name="citation_title" content="A Paper"><meta name="citation_abstract" content="Complete abstract from the alternate version.">')
        assert request.url.host != 'wrong.example'
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = ScholarProvider(http, EmptyMetadata(), backend='serpapi')
        paper = await provider.paper({'title': 'A Paper', 'result_id': '1',
                                     'snippet': 'Short excerpt …', 'link': 'https://blocked.example/paper'})
    assert paper.abstract_status == 'complete'
    assert paper.abstract == 'Complete abstract from the alternate version.'
    assert paper.abstract_source == 'https://alternate.example/paper'


async def test_github_pages_pdf_source_fallback_and_column_order(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('papergraph.retrieval.scholar.shutil.which', lambda _: 'pdftotext')
    def extract(command, **kwargs):
        assert '-raw' in command
        return SimpleNamespace(stdout=(
            'A Paper\nAbstract\nThis complete abstract describes hetero-\ngeneous serving '
            'and contains enough text to verify extraction without mixing columns.\n1 Introduction\nBody\nCHASM @ MICRO 2026, November 1, 2026, Athens, Greece').encode())
    monkeypatch.setattr('papergraph.retrieval.scholar.subprocess.run', extract)
    def handler(request):
        if request.url.host == 'author.github.io':
            return httpx.Response(403)
        assert str(request.url) == 'https://raw.githubusercontent.com/author/author.github.io/master/paper.pdf'
        return httpx.Response(200, content=b'%PDF-1.7 mock')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        paper = Paper(id='1', openalex_id=None, title='A Paper')
        abstract, source = await ScholarProvider(http, None)._landing_metadata(
            paper, {'link': 'https://author.github.io/paper.pdf'})
    assert 'heterogeneous' in abstract and 'Body' not in abstract
    assert source == paper.pdf_url
    assert source.startswith('https://raw.githubusercontent.com/')
    assert paper.year == 2026


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
