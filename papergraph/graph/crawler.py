"""Fetch each neighborhood once, then compute provenance per real seed."""

from collections import defaultdict, deque

from papergraph.models import (
    CitationEdge, CitationGraph, CitationPath, CrawlConfig, Paper,
    PaperNode, PaperSeedRelation,
)
from papergraph.retrieval.openalex import OpenAlexClient


class CitationCrawler:
    def __init__(self, provider: OpenAlexClient):
        self.provider = provider

    async def crawl(self, seeds: list[Paper], config: CrawlConfig | None = None) -> CitationGraph:
        config = config or CrawlConfig()
        papers = {p.id: p for p in seeds}
        if not papers:
            raise ValueError("At least one resolved seed is required")
        if len(papers) > config.max_papers:
            raise ValueError("max_papers must be at least the number of unique seeds")
        seed_ids = sorted(papers)
        frontier = seed_ids
        edges: set[tuple[str, str]] = set()
        reasons: set[str] = set()
        warnings: set[str] = set()
        directions = ["citing", "references"] if config.direction == "both" else [config.direction]
        for hop in range(1, config.depth + 1):
            next_frontier = set()
            for parent_id in frontier:
                for direction in directions:
                    neighbors, limited, missing = await self.provider.neighbors(
                        papers[parent_id], direction, config.max_neighbors,
                    )
                    if limited:
                        reasons.add(f"max_neighbors:{parent_id}:{direction}")
                    if missing:
                        warnings.add(f"Unresolved references of {parent_id}: {','.join(missing)}")
                    for neighbor in neighbors:
                        if neighbor.id not in papers:
                            if len(papers) >= config.max_papers:
                                reasons.add("max_papers")
                                continue
                            if len(next_frontier) >= config.max_papers_per_hop:
                                reasons.add(f"max_papers_per_hop:{hop}")
                                continue
                            papers[neighbor.id] = neighbor
                            next_frontier.add(neighbor.id)
                        edge = ((neighbor.id, parent_id) if direction == "citing"
                                else (parent_id, neighbor.id))
                        edges.add(edge)
            frontier = sorted(next_frontier)
            if not frontier:
                break
        # Include all observed internal edges, including edges between boundary nodes.
        # Unknown outside references do not create nodes beyond the requested depth.
        for paper in papers.values():
            for ref in paper.referenced_works:
                target = self.provider.aliases.get(ref, ref)
                if target in papers:
                    edges.add((paper.id, target))
        adjacency: dict[str, set[str]] = defaultdict(set)
        for source, target in edges:
            if config.direction in {"references", "both"}:
                adjacency[source].add(target)
            if config.direction in {"citing", "both"}:
                adjacency[target].add(source)
        relations, paths = self._provenance(seed_ids, adjacency, config)
        by_paper = defaultdict(list)
        for relation in relations:
            by_paper[relation.paper_id].append(relation)
        nodes = []
        for identifier, paper in sorted(papers.items()):
            relevant = by_paper[identifier]
            nodes.append(PaperNode(
                **paper.model_dump(), hop=min(r.min_hop for r in relevant),
                seed_ids=sorted(r.seed_id for r in relevant),
                parent_ids=sorted({p for r in relevant for p in r.parent_ids}),
            ))
        if any(r.paths_truncated for r in relations):
            reasons.add("max_paths_per_pair")
        return CitationGraph(
            config=config, seed_ids=seed_ids, nodes=nodes,
            edges=[CitationEdge(source_id=s, target_id=t, provider=getattr(self.provider, 'source', 'openalex')) for s, t in sorted(edges)],
            paths=paths, seed_relations=relations, truncated=bool(reasons),
            truncation_reasons=sorted(reasons), warnings=sorted(warnings),
            openalex_requests=self.provider.requests,
        )

    @staticmethod
    def _provenance(seed_ids, adjacency, config):
        relations, paths = [], []
        for seed in seed_ids:
            distance = {seed: 0}
            parents = defaultdict(set)
            queue = deque([seed])
            while queue:
                parent = queue.popleft()
                hop = distance[parent] + 1
                if hop > config.depth:
                    continue
                for child in sorted(adjacency[parent]):
                    if child not in distance:
                        distance[child] = hop
                        queue.append(child)
                    if distance[child] == hop:
                        parents[child].add(parent)
            # Keep cap+1 internally to propagate overflow through descendants.
            materialized = {seed: [[seed]]}
            for child in sorted(distance, key=lambda p: (distance[p], p)):
                if child != seed:
                    options = []
                    for parent in sorted(parents[child]):
                        for prefix in materialized[parent]:
                            options.append(prefix + [child])
                            if len(options) > config.max_paths_per_pair:
                                break
                        if len(options) > config.max_paths_per_pair:
                            break
                    materialized[child] = options
                paths_for_child = materialized[child]
                relations.append(PaperSeedRelation(
                    seed_id=seed, paper_id=child, min_hop=distance[child],
                    parent_ids=sorted(parents[child]),
                    paths_truncated=len(paths_for_child) > config.max_paths_per_pair,
                ))
                paths.extend(CitationPath(seed_id=seed, paper_id=child, path=path,
                                          hop=distance[child])
                             for path in paths_for_child[:config.max_paths_per_pair])
        return relations, paths
