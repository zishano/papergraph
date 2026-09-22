import httpx
import pytest

from papergraph.retrieval.openalex import OpenAlexClient, RetrievalError, paper_from_work
from papergraph.retrieval.resolver import ResolutionError, SeedResolver, arxiv_id


def work(n=1, title="A Test Paper", doi="https://doi.org/10.1234/test"):
    return {"id": f"https://openalex.org/W{n}", "title": title, "doi": doi}


async def test_doi_id_arxiv_title_and_aliases():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/works":
            return httpx.Response(200, json={"results": [work()]})
        return httpx.Response(200, json=work())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAlexClient(http)
        resolver = SeedResolver(provider)
        seeds = ["https://doi.org/10.1234/TEST", "W1", "https://openalex.org/w1",
                 "A Test Paper", "a test paper", "arXiv:2301.01234v2",
                 "https://arxiv.org/pdf/2301.01234.pdf"]
        papers = await resolver.resolve_many(seeds)
        assert len(papers) == 1
        assert papers[0].arxiv_id == "2301.01234"
        assert len(calls) == 3  # DOI + title + arXiv DOI; aliases never re-request.


@pytest.mark.parametrize("seed", ["", "  ", "a.pdf", "doi:broken", "arxiv:broken", "https://example.com/x", "!!!"])
async def test_bad_seed_does_not_make_network_request(seed):
    def handler(request):
        pytest.fail("Invalid input should not reach the network")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ResolutionError):
            await SeedResolver(OpenAlexClient(http)).resolve(seed)


@pytest.mark.parametrize("results", [[], [work(title="Different")], [work(), work(2)]])
async def test_title_missing_or_ambiguous(results):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"results": results})
    )) as http:
        with pytest.raises(ResolutionError):
            await SeedResolver(OpenAlexClient(http)).resolve("A Test Paper")


async def test_title_with_colon_is_not_a_url():
    title = "OpenAlex: A fully-open index"
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"results": [work(title=title)]})
    )) as http:
        assert (await SeedResolver(OpenAlexClient(http)).resolve(title)).id == "W1"


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_provider_errors_not_empty_results_or_key_leaks(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, text="secret-api-key")
    )) as http:
        with pytest.raises(RetrievalError) as exc:
            await OpenAlexClient(http, api_key="secret-api-key").get_work("W1")
        assert "secret-api-key" not in str(exc.value)


async def test_arxiv_atom_fallback():
    def handler(request):
        if request.url.host == "export.arxiv.org":
            assert "authorization" not in request.headers
            return httpx.Response(200, text='''<feed xmlns="http://www.w3.org/2005/Atom">
              <entry><id>http://arxiv.org/abs/hep-th/9901001v2</id>
              <title>A Test Paper</title></entry></feed>''')
        if request.url.path == "/works":
            return httpx.Response(200, json={"results": [work()]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        resolver = SeedResolver(OpenAlexClient(http, api_key="only-for-openalex"))
        paper = await resolver.resolve("hep-th/9901001")
        assert paper.id == "W1"
        assert paper.arxiv_id == "hep-th/9901001"
        assert resolver.arxiv_requests == 1


def test_missing_metadata_and_abstract_positions():
    paper = paper_from_work(work(doi=None))
    assert paper.doi is paper.abstract is paper.pdf_url is None
    data = work()
    data["abstract_inverted_index"] = {"world": [1, 3], "Hello": [0], "again": [2]}
    assert paper_from_work(data).abstract == "Hello world again world"
    paper.authors.append("one")
    assert paper_from_work(work()).authors == []


@pytest.mark.parametrize("value,expected", [
    ("2309.06180v2", "2309.06180"),
    ("https://arxiv.org/abs/2309.06180", "2309.06180"),
    ("https://arxiv.org/pdf/hep-th/9901001v1.pdf", "hep-th/9901001"),
    ("arXiv:cs.AI/9901001", "cs.ai/9901001"),
])
def test_arxiv_normalization(value, expected):
    assert arxiv_id(value) == expected
