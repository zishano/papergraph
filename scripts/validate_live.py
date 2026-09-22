"""Explicit network acceptance check. Run from the project root.

PYTHONPATH=. python scripts/validate_live.py
Only public metadata is saved. No API key/header is recorded.
"""

import asyncio
from collections import Counter, deque
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx

from papergraph.graph.crawler import CitationCrawler
from papergraph.models import CrawlConfig
from papergraph.retrieval.openalex import OpenAlexClient
from papergraph.retrieval.resolver import ResolutionError, SeedResolver


async def validate():
    output = Path("validation")
    output.mkdir(exist_ok=True)
    captured = []

    async def capture(response):
        await response.aread()
        captured.append({"url": str(response.request.url), "status": response.status_code,
                         "body": response.json()})

    async with httpx.AsyncClient(timeout=45, event_hooks={"response": [capture]}) as http:
        provider = OpenAlexClient(http)
        resolver = SeedResolver(provider)
        inputs = ["10.7717/peerj.4375", "W2741809807",
                  "OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts",
                  "arXiv:2309.06180"]
        papers = await resolver.resolve_many(inputs)
        assert len(papers) == 3 and provider.requests == 3
        m1 = {"inputs": inputs, "papers": [p.model_dump() for p in papers],
              "requests": {"openalex": provider.requests, "arxiv": resolver.arxiv_requests}}
        try:
            await resolver.resolve("The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles")
        except ResolutionError as exc:
            m1["ambiguous_title_check"] = str(exc)
        else:
            m1["ambiguous_title_check"] = "Provider now returned a unique exact title"
        (output / "m1-live.json").write_text(json.dumps(m1, indent=2, ensure_ascii=False) + "\n")

        results = []
        for name, seed_inputs, direction in [
            ("single-citing", ["arXiv:2309.06180"], "citing"),
            ("single-references", ["W2741809807"], "references"),
            ("multi-both", ["W4386721862", "W4288680697", "arXiv:2309.06180"], "both"),
        ]:
            before = provider.requests
            seeds = await resolver.resolve_many(seed_inputs)
            config = CrawlConfig(depth=2, direction=direction, max_papers=40,
                                 max_papers_per_hop=25, max_neighbors=5, max_paths_per_pair=10)
            graph = await CitationCrawler(provider).crawl(seeds, config)
            nodes = {n.id: n for n in graph.nodes}
            assert len(nodes) == len(graph.nodes)
            assert any(n.hop == 2 for n in graph.nodes), f"{name}: no real hop 2 found"
            # Audit stored edge direction against provider metadata, not path order.
            for edge in graph.edges:
                assert edge.target_id in nodes[edge.source_id].referenced_works, edge
            edges = {(e.source_id, e.target_id) for e in graph.edges}
            distances = {}
            for seed in graph.seed_ids:
                dist = {seed: 0}
                q = deque([seed])
                while q:
                    parent = q.popleft()
                    if dist[parent] == config.depth:
                        continue
                    for child in nodes:
                        connected = ((direction in {"citing", "both"} and (child, parent) in edges)
                                     or (direction in {"references", "both"} and (parent, child) in edges))
                        if connected and child not in dist:
                            dist[child] = dist[parent] + 1
                            q.append(child)
                distances[seed] = dist
            assert {(r.seed_id, r.paper_id): r.min_hop for r in graph.seed_relations} == {
                (seed, paper): hop for seed, dist in distances.items() for paper, hop in dist.items()}
            for path in graph.paths:
                assert path.hop == distances[path.seed_id][path.paper_id] == len(path.path) - 1
                for parent, child in zip(path.path, path.path[1:]):
                    assert ((direction in {"citing", "both"} and (child, parent) in edges)
                            or (direction in {"references", "both"} and (parent, child) in edges))
            counts = dict(sorted(Counter(n.hop for n in graph.nodes).items()))
            example = next(p for p in graph.paths if p.hop == 2)
            summary = {"case": name, "nodes_by_hop": counts, "edges": len(graph.edges),
                       "paths": len(graph.paths), "network_requests": provider.requests - before,
                       "truncated": graph.truncated, "warnings": graph.warnings,
                       "example_path": [{"id": i, "title": nodes[i].title} for i in example.path]}
            results.append(summary)
            (output / f"m2-{name}.json").write_text(graph.model_dump_json(indent=2) + "\n")
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        urls = [row["url"] for row in captured]
        duplicates = {url: count for url, count in Counter(urls).items() if count > 1}
        assert not duplicates, duplicates
        summary = {"verified_at_utc": datetime.now(timezone.utc).isoformat(),
                   "m1_requests": m1["requests"], "m2": results,
                   "total_network_requests": len(captured), "duplicate_requests": duplicates,
                   "database_check": "N/A: M3 not implemented",
                   "scope": "M1 and M2 only; bounded live sample, not exhaustive citation neighborhoods"}
        (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        # Provider projections are sufficient to replay API contracts without huge location lists.
        keep = {"id", "title", "display_name", "doi", "publication_year", "authorships",
                "abstract_inverted_index", "referenced_works", "cited_by_count",
                "primary_location", "best_oa_location"}
        for item in captured:
            body = item["body"]
            if "results" in body:
                item["body"] = {"meta": body.get("meta", {}),
                                "results": [{k: v for k, v in w.items() if k in keep} for w in body["results"]]}
            elif "id" in body:
                item["body"] = {k: v for k, v in body.items() if k in keep}
        (output / "openalex-responses.json").write_text(json.dumps(captured, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(validate())
