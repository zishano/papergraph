"""Replay actual OpenAlex metadata, without network or credentials."""

import json
from pathlib import Path

import httpx

from papergraph.graph.crawler import CitationCrawler
from papergraph.models import CrawlConfig
from papergraph.retrieval.openalex import OpenAlexClient
from papergraph.retrieval.resolver import ResolutionError, SeedResolver


async def test_recorded_live_resolution_and_three_graphs():
    root = Path(__file__).resolve().parents[1] / "validation"
    responses = {row["url"]: row for row in json.loads((root / "openalex-responses.json").read_text())}
    requested = []

    def handler(request):
        url = str(request.url)
        assert url not in requested, f"Repeated real-data request: {url}"
        requested.append(url)
        row = responses[url]
        return httpx.Response(row["status"], json=row["body"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAlexClient(http, api_key="")
        resolver = SeedResolver(provider)
        expected_m1 = json.loads((root / "m1-live.json").read_text())
        papers = await resolver.resolve_many(expected_m1["inputs"])
        assert [p.model_dump() for p in papers] == expected_m1["papers"]
        try:
            await resolver.resolve("The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles")
        except ResolutionError:
            pass
        for name, inputs in [
            ("single-citing", ["arXiv:2309.06180"]),
            ("single-references", ["W2741809807"]),
            ("multi-both", ["W4386721862", "W4288680697", "arXiv:2309.06180"]),
        ]:
            expected = json.loads((root / f"m2-{name}.json").read_text())
            seeds = await resolver.resolve_many(inputs)
            graph = await CitationCrawler(provider).crawl(seeds, CrawlConfig(**expected["config"]))
            assert graph.model_dump() == expected
        assert len(requested) == len(responses)
