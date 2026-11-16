import httpx

from papergraph.models import Paper
from papergraph.retrieval.openalex import paper_from_work
from papergraph.retrieval.scholar import ScholarProvider


def location(name, kind, **flags):
    return {'source': {'type': kind, 'display_name': name}, **flags}


def test_published_location_preferred_over_repository():
    paper = paper_from_work({'id': 'https://openalex.org/W1', 'title': 'A paper',
        'primary_location': location('arXiv', 'repository'),
        'locations': [location('Conference on Systems', 'conference', is_published=True,
                               landing_page_url='https://publisher.example/article')]})
    assert paper.venue == 'Conference on Systems'
    assert paper.publication_status == 'published'
    assert paper.publication_source == 'https://publisher.example/article'


def test_repository_is_not_acceptance_evidence():
    paper = paper_from_work({'id': 'https://openalex.org/W1', 'title': 'A paper',
                            'primary_location': location('arXiv', 'repository', is_published=True)})
    assert paper.publication_status == 'preprint'
    unknown = paper_from_work({'id': 'https://openalex.org/W2', 'title': 'Other'})
    assert unknown.publication_status == 'unknown'


def test_accepted_location_is_distinct_from_published():
    paper = paper_from_work({'id': 'https://openalex.org/W1', 'title': 'A paper',
        'primary_location': location('Journal X', 'journal', version='acceptedVersion')})
    assert paper.publication_status == 'accepted'


def version(id, authors, year=2026, status='preprint', doi=None):
    return Paper(id=id, openalex_id=id, title='A paper', authors=authors, year=year,
                 publication_status=status, doi=doi, abstract='Complete abstract.')


def test_strong_version_match_preserves_identifiers():
    candidates = [version('W1', ['Alice Researcher', 'Bob Expert']),
                  version('W2', ['Alice Researcher', 'Bob Expert'], status='published')]
    selected = ScholarProvider._select_versions(candidates)
    assert len(selected) == 1 and selected[0].id == 'W2'
    assert selected[0].version_ids == ['W1', 'W2']
    assert selected[0].metadata_match == 'matched_versions'


def test_title_only_and_conflicting_authors_remain_ambiguous():
    for authors in ([], ['Someone Else', 'Another Person']):
        candidates = [version('W1', ['Alice Researcher', 'Bob Expert']), version('W2', authors)]
        assert len(ScholarProvider._select_versions(candidates)) == 2
    assert len(ScholarProvider._select_versions([
        version('W1', ['A', 'B'], year=2020), version('W2', ['A', 'B'], year=2026)])) == 2


async def test_preprint_identifier_can_upgrade_to_confirmed_published_version():
    preprint = version('W1', ['Alice Researcher', 'Bob Expert'], doi='10.48550/arxiv.2601.12345')
    published = version('W2', preprint.authors, status='published', doi='10.1234/published')
    published.venue = 'Systems Conference'
    class Metadata:
        async def get_work(self, identifier):
            return preprint
        async def search_title(self, title):
            return [preprint, published]
    provider = ScholarProvider(None, Metadata())
    matched = await provider._openalex_matches({'link': 'https://arxiv.org/abs/2601.12345'}, 'A paper')
    assert matched[0].id == 'W2'
    assert preprint.doi in matched[0].version_ids


async def test_metadata_cache_does_not_restore_old_citation_identifier(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.sqlite'))
    class Metadata:
        calls = 0
        async def search_title(self, title):
            self.calls += 1
            return [version('W1', ['A', 'B'])]
    metadata = Metadata()
    async with httpx.AsyncClient() as http:
        first = ScholarProvider(http, metadata)
        await first.paper({'title': 'A paper', 'result_id': 'old', 'link': 'https://example.org/paper'})
        second = ScholarProvider(http, metadata)
        paper = await second.paper({'title': 'A paper', 'result_id': 'new', 'link': 'https://example.org/paper'})
        assert metadata.calls == 1
        assert paper.id == 'scholar:new' and paper.referenced_works == []
        assert second.entries[paper.id]['result_id'] == 'new'
        monkeypatch.setenv('PAPERGRAPH_REFRESH', '1')
        await ScholarProvider(http, metadata).paper({'title': 'A paper', 'result_id': 'new', 'link': 'https://example.org/paper'})
        assert metadata.calls == 2


async def test_same_title_different_authors_remain_distinct_scholar_nodes():
    class Metadata:
        async def search_title(self, title):
            return [version('W1', ['Alice Researcher', 'Bob Expert']),
                    version('W2', ['Carol Scientist', 'Dana Author'])]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        provider = ScholarProvider(http, Metadata(), csv_path='unused.csv')
        one = await provider.paper({'title': 'A paper', 'result_id': 'one', 'publication_info': {
            'authors': [{'name': 'Alice Researcher'}, {'name': 'Bob Expert'}]}})
        two = await provider.paper({'title': 'A paper', 'result_id': 'two', 'publication_info': {
            'authors': [{'name': 'Carol Scientist'}, {'name': 'Dana Author'}]}})
    assert one.id != two.id
    assert one.metadata_match == two.metadata_match == 'ambiguous'


async def test_incomplete_scholar_metadata_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.sqlite'))
    class Metadata:
        calls = 0
        async def search_title(self, title):
            self.calls += 1
            return []
    metadata = Metadata()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        for _ in range(2):
            provider = ScholarProvider(http, metadata, csv_path='unused.csv')
            await provider.paper({'title': 'A paper', 'result_id': 'one', 'snippet': 'Incomplete …'})
    assert metadata.calls == 2


async def test_metadata_cache_separates_same_title_authors_without_links(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.sqlite'))
    class Metadata:
        def __init__(self, authors):
            self.authors = authors
            self.calls = 0
        async def search_title(self, title):
            self.calls += 1
            return [version('W' + self.authors[0], self.authors)]
    async with httpx.AsyncClient() as http:
        for authors in (['Alice Researcher', 'Bob Expert'], ['Carol Scientist', 'Dana Author']):
            metadata = Metadata(authors)
            entry = {'title': 'A paper', 'result_id': 'same-result', 'publication_info': {
                'authors': [{'name': name} for name in authors]}}
            paper = await ScholarProvider(http, metadata).paper(entry)
            assert metadata.calls == 1
            assert paper.authors == authors
            cached = await ScholarProvider(http, metadata).paper(entry)
            assert metadata.calls == 1
            assert cached.authors == authors


async def test_title_only_complete_metadata_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERGRAPH_CACHE_PATH', str(tmp_path / 'cache.sqlite'))
    class Metadata:
        calls = 0
        async def search_title(self, title):
            self.calls += 1
            return [version('W1', ['A', 'B'])]
    metadata = Metadata()
    async with httpx.AsyncClient() as http:
        for link in (None, None, 'https://example.org/', 'https://example.org/'):
            await ScholarProvider(http, metadata).paper({'title': 'A paper', 'result_id': 'one', 'link': link})
    assert metadata.calls == 4
