from collections import Counter

import httpx
import pytest
from pydantic import ValidationError

from papergraph.graph.crawler import CitationCrawler
from papergraph.models import CrawlConfig
from papergraph.retrieval.openalex import OpenAlexClient, RetrievalError


def record(n, refs=()):
    return {"id": f"https://openalex.org/W{n}", "title": f"Paper {n}",
            "referenced_works": [f"https://openalex.org/W{r}" for r in refs]}


class FixtureAPI:
    def __init__(self, records, page_size=200):
        self.records = {r["id"].split("/")[-1]: r for r in records}
        self.calls = []
        self.page_size = page_size

    def __call__(self, request):
        self.calls.append(str(request.url))
        if request.url.path != "/works":
            return httpx.Response(200, json=self.records[request.url.path.split("/")[-1]])
        params = request.url.params
        field, value = params["filter"].split(":", 1)
        if field == "openalex":
            results = [self.records[k] for k in value.split("|") if k in self.records]
            return httpx.Response(200, json={"results": results})
        assert field == "cites"
        assert params["sort"] == "publication_date:asc"
        rows = [r for _, r in sorted(self.records.items())
                if f"https://openalex.org/{value}" in r["referenced_works"]]
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
        size = min(self.page_size, int(params["per_page"]))
        results = rows[offset:offset + size]
        next_cursor = str(offset + size) if offset + size < len(rows) else None
        return httpx.Response(200, json={"results": results, "meta": {"next_cursor": next_cursor}})


async def build(records, seed_ids=(1,), **kwargs):
    api = FixtureAPI(records, page_size=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
        provider = OpenAlexClient(http)
        seeds = [await provider.get_work(f"W{n}") for n in seed_ids]
        graph = await CitationCrawler(provider).crawl(seeds, CrawlConfig(**kwargs))
        assert max(Counter(api.calls).values()) == 1
        return graph, api


def assert_invariants(graph):
    nodes = {n.id: n for n in graph.nodes}
    edges = {(e.source_id, e.target_id) for e in graph.edges}
    assert len(nodes) == len(graph.nodes)
    assert len(edges) == len(graph.edges)
    for source, target in edges:
        assert source in nodes and target in nodes
    for p in graph.paths:
        assert p.path[0] == p.seed_id and p.path[-1] == p.paper_id
        assert len(p.path) == len(set(p.path))
        assert len(p.path) - 1 == p.hop <= graph.config.depth
        for parent, child in zip(p.path, p.path[1:]):
            if graph.config.direction == "citing":
                assert (child, parent) in edges
            elif graph.config.direction == "references":
                assert (parent, child) in edges
            else:
                assert (parent, child) in edges or (child, parent) in edges
    for node in nodes.values():
        relations = [r for r in graph.seed_relations if r.paper_id == node.id]
        assert node.hop == min(r.min_hop for r in relations)
        assert node.seed_ids == sorted(r.seed_id for r in relations)


async def test_diamond_multi_parent_and_cursor_pagination():
    graph, api = await build([record(1), record(2, [1]), record(3, [1]), record(4, [2, 3]), record(5, [4])])
    assert_invariants(graph)
    assert {n.id for n in graph.nodes} == {"W1", "W2", "W3", "W4"}
    node = next(n for n in graph.nodes if n.id == "W4")
    assert node.parent_ids == ["W2", "W3"]
    assert [p.path for p in graph.paths if p.paper_id == "W4"] == [["W1", "W2", "W4"], ["W1", "W3", "W4"]]
    assert not graph.truncated
    assert not any("cites%3AW4" in c for c in api.calls)


async def test_multiple_seeds_propagate_shared_descendants_without_requery():
    # W3 is one hop from W1 but two hops from W2, and is expanded only once.
    graph, api = await build([record(1), record(2), record(3, [1, 5]),
                              record(4, [3]), record(5, [2])], seed_ids=(1, 2, 1), depth=3)
    assert_invariants(graph)
    relations = {(r.seed_id, r.paper_id): r.min_hop for r in graph.seed_relations}
    assert relations["W1", "W4"] == 2
    assert relations["W2", "W4"] == 3
    assert [p.path for p in graph.paths if p.seed_id == "W2" and p.paper_id == "W4"] == [["W2", "W5", "W3", "W4"]]
    assert sum("cites%3AW3" in c for c in api.calls) == 1


@pytest.mark.parametrize("direction", ["citing", "references", "both"])
async def test_cycles_direction_and_boundary_edges(direction):
    graph, _ = await build([record(1, [3]), record(2, [1]), record(3, [2]), record(4, [3])], direction=direction)
    assert_invariants(graph)
    edges = {(e.source_id, e.target_id) for e in graph.edges}
    assert {("W1", "W3"), ("W2", "W1"), ("W3", "W2")} <= edges
    if direction == "citing":
        assert {n.id for n in graph.nodes} == {"W1", "W2", "W3"}
    if direction == "references":
        assert [p.path for p in graph.paths if p.paper_id == "W2"] == [["W1", "W3", "W2"]]


async def test_depth_zero_makes_no_neighbor_requests():
    graph, api = await build([record(1, [2]), record(2)], depth=0)
    assert len(api.calls) == 1
    assert [p.path for p in graph.paths] == [["W1"]]
    assert len(graph.nodes) == 1


@pytest.mark.parametrize("limits,reason", [
    ({"max_papers": 2}, "max_papers"),
    ({"max_papers_per_hop": 1}, "max_papers_per_hop:1"),
    ({"max_neighbors": 1}, "max_neighbors:W1:citing"),
])
async def test_node_caps_are_explicit(limits, reason):
    graph, _ = await build([record(1), record(2, [1]), record(3, [1]), record(4, [2])], **limits)
    assert_invariants(graph)
    assert graph.truncated and reason in graph.truncation_reasons
    if "max_papers" in limits:
        assert len(graph.nodes) <= limits["max_papers"]


async def test_path_cap_preserves_all_parents_and_propagates_overflow():
    graph, _ = await build([record(1), record(2, [1]), record(3, [1]),
                            record(4, [2, 3]), record(5, [4])], depth=3, max_paths_per_pair=1)
    assert_invariants(graph)
    for identifier in ["W4", "W5"]:
        assert len([p for p in graph.paths if p.paper_id == identifier]) == 1
        assert next(r for r in graph.seed_relations if r.paper_id == identifier).paths_truncated
    assert next(n for n in graph.nodes if n.id == "W4").parent_ids == ["W2", "W3"]


async def test_reference_missing_metadata_is_reported():
    graph, _ = await build([record(1, [2, 999]), record(2)], direction="references")
    assert len(graph.nodes) == 2
    assert graph.warnings == ["Unresolved references of W1: W999"]
    assert_invariants(graph)


async def test_reference_cap_and_batches_reuse_known_metadata():
    records = [record(1, range(2, 104))] + [record(i) for i in range(2, 104)]
    graph, api = await build(records, direction="references", depth=1, max_neighbors=101)
    assert len(graph.nodes) == 102
    assert "max_neighbors:W1:references" in graph.truncation_reasons
    assert len(api.calls) == 3  # seed + two batches, not 101 individual requests.


async def test_seed_count_exceeding_budget_rejected():
    with pytest.raises(ValueError, match="number of unique seeds"):
        await build([record(1), record(2)], seed_ids=(1, 2), max_papers=1)


@pytest.mark.parametrize("config", [{"depth": -1}, {"depth": 4}, {"max_neighbors": 0},
                                    {"max_papers": 0}, {"max_papers_per_hop": 0},
                                    {"max_paths_per_pair": 0}, {"direction": "invalid"}])
def test_invalid_bounds(config):
    with pytest.raises(ValidationError):
        CrawlConfig(**config)


async def test_repeated_cursor_fails_instead_of_looping():
    def handler(request):
        return httpx.Response(200, json={"results": [record(2, [1])], "meta": {"next_cursor": "*"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAlexClient(http)
        seed = provider.remember(record(1))
        with pytest.raises(RetrievalError, match="repeated"):
            await CitationCrawler(provider).crawl([seed])
