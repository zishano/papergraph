import json
import sqlite3
import time

import httpx
import pytest

from papergraph.cache import Cache, CachedClient
from papergraph.gemini import GeminiScorer
from papergraph.models import CitationGraph, CrawlConfig, PaperNode


async def test_cache_survives_clients_and_redacts_keys(tmp_path, monkeypatch):
    path = tmp_path / 'cache.db'
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(path))
    monkeypatch.setenv('SERPAPI_API_KEY', 'sensitive-test')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'organic_results': [], 'next': 'https://serpapi.com/?api_key=sensitive-test'})
    for _ in range(2):
        async with CachedClient(transport=httpx.MockTransport(handler)) as client:
            response = await client.get('https://serpapi.com/search.json?api_key=sensitive-test&q=paper')
            assert response.json()['organic_results'] == []
    assert len(calls) == 1
    assert b'sensitive-test' not in path.read_bytes()
    monkeypatch.setenv('PAPERGRAPH_REFRESH', '1')
    async with CachedClient(transport=httpx.MockTransport(handler)) as client:
        await client.get('https://serpapi.com/search.json?api_key=sensitive-test&q=paper')
    assert len(calls) == 2


async def test_errors_are_never_cached(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.db'))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={'error': 'quota'}) if len(calls) == 1 else httpx.Response(200, json={'ok': True})
    async with CachedClient(transport=httpx.MockTransport(handler)) as client:
        assert (await client.get('https://api.openalex.org/works')).status_code == 429
        assert (await client.get('https://api.openalex.org/works')).status_code == 200
        await client.get('https://api.openalex.org/works')
    assert len(calls) == 2


def test_expiration(tmp_path):
    cache = Cache(tmp_path / 'cache.db')
    cache.put('a', {'ok': True}, -1)
    assert Cache(cache.path).get('a') is None


async def test_ai_reuses_successes_but_invalidates_changed_abstract_and_keywords(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.db'))
    node = PaperNode(id='p', title='Paper', openalex_id=None, abstract='Full abstract', abstract_status='complete',
                     hop=1, parent_ids=['seed'], seed_ids=['seed'])
    graph = CitationGraph(config=CrawlConfig(), seed_ids=['seed'], nodes=[node], edges=[], paths=[], seed_relations=[])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(
            {'score': 70, 'summary': 'Summary', 'reason': 'Reason', 'translation': 'Translation'})}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async def analyze(keywords):
            return await GeminiScorer(client, api_key='test', model='test', interval=0).analyze(graph, keywords)
        await analyze('DSE')
        result = await analyze('DSE')
        assert result[0]['cached'] and len(calls) == 1
        await analyze('other')
        node.abstract = 'Changed full abstract'
        await analyze('DSE')
    assert len(calls) == 3


async def test_graph_checkpoint_resumes_without_refetch(tmp_path, monkeypatch):
    from papergraph.cli import parser, run
    from papergraph.retrieval.scholar import ScholarProvider
    from papergraph.graph.crawler import CitationCrawler
    monkeypatch.setenv('PAPERGRAPH_CHECKPOINT_DIR', str(tmp_path))
    graph = CitationGraph(config=CrawlConfig(), seed_ids=['seed'], nodes=[], edges=[], paths=[], seed_relations=[])
    (tmp_path / 'graph.json').write_text(graph.model_dump_json())
    async def no_network(*args):
        pytest.fail('Completed graph must be restored')
    monkeypatch.setattr(ScholarProvider, 'resolve', no_network)
    monkeypatch.setattr(CitationCrawler, 'crawl', no_network)
    args = parser().parse_args(['search', '--source', 'scholar', '--seed', 'Seed', '--no-gemini'])
    result = await run(args)
    assert result['seed_ids'] == ['seed']
