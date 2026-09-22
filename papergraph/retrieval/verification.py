"""Discover arXiv candidates and verify their bibliographies against graph seeds."""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import re
import time
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import httpx

from papergraph.graph.crawler import CitationCrawler
from papergraph.models import CitationEdge, CitationGraph, PaperNode
from papergraph.retrieval.openalex import OpenAlexClient, RetrievalError
from papergraph.retrieval.resolver import arxiv_id, normalize_title


def keyword_terms(value: str) -> list[str]:
    """Expand user search hints without turning them into graph filters."""
    terms = [term.strip() for term in re.split(r"[,，]", value) if term.strip()]
    expanded = []
    for term in terms:
        expanded.append(term)
        if term.casefold() == "dse":
            expanded.append("Design Space Exploration")
    return list(dict.fromkeys(expanded))


class ArxivCandidateDiscovery:
    """Find recent arXiv papers whose metadata contains an auxiliary keyword."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.requests = 0

    async def search(self, keywords: str, limit: int = 20) -> list[dict]:
        terms = keyword_terms(keywords)
        if not terms or limit < 1:
            return []
        query = " OR ".join(f'all:"{term.replace(chr(34), "")}"' for term in terms)
        try:
            self.requests += 1
            response = await self.http.get("https://export.arxiv.org/api/query", params={
                "search_query": query,
                "start": 0,
                "max_results": limit,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            })
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except (httpx.HTTPError, ET.ParseError):
            return await self._search_web(terms, limit)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        candidates = []
        for entry in root.findall("a:entry", ns):
            identifier = arxiv_id(entry.findtext("a:id", "", ns))
            title = " ".join(entry.findtext("a:title", "", ns).split())
            if identifier and title:
                candidates.append({"arxiv_id": identifier, "title": title,
                                   "source": "arxiv_keyword_search"})
        return candidates

    async def _search_web(self, terms: list[str], limit: int) -> list[dict]:
        # Prefer the descriptive expansion over a short acronym. This keeps the
        # candidate set focused enough for bibliography verification.
        query = max(terms, key=len)
        page_size = next((size for size in (25, 50, 100, 200) if size >= limit), 200)
        try:
            self.requests += 1
            response = await self.http.get("https://arxiv.org/search/", params={
                "query": f'"{query}"',
                "searchtype": "title",
                "abstracts": "show",
                "order": "-announced_date_first",
                "size": page_size,
            }, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PaperGraph/0.1; citation-research)",
                "Accept": "text/html,application/xhtml+xml",
            })
            response.raise_for_status()
        except httpx.HTTPError:
            raise RetrievalError("arXiv candidate discovery failed") from None
        html = BeautifulSoup(response.text, "html.parser")
        candidates = []
        for item in html.select("li.arxiv-result"):
            link = item.select_one("p.list-title a[href*='/abs/']")
            title_node = item.select_one("p.title")
            identifier = arxiv_id(link.get("href", "")) if link else None
            title = title_node.get_text(" ", strip=True) if title_node else ""
            if identifier and title:
                candidates.append({"arxiv_id": identifier, "title": title,
                                   "source": "arxiv_keyword_search"})
        if not candidates and html.select_one("form") is None:
            raise RetrievalError("arXiv candidate discovery returned an unrecognized page")
        return candidates[:limit]


class ArxivVerifier:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.last_request = 0.0
        self.cache = {}

    async def fetch(self, url):
        if url not in self.cache:
            await asyncio.sleep(max(0, 3 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = await self.http.get(url, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError:
                raise RetrievalError('arXiv HTML unavailable; citation remains unverified') from None
            self.cache[url] = response.text
        return BeautifulSoup(self.cache[url], 'html.parser')

    async def verify(self, graph: CitationGraph, candidates: list[dict], provider: OpenAlexClient):
        if graph.config.direction != 'citing' or graph.config.depth < 1:
            raise ValueError('Supplement verification requires citing direction and depth >= 1')
        nodes = {n.id: n for n in graph.nodes}
        records = []
        seen = set()
        for candidate in candidates:
            aid = arxiv_id(candidate['arxiv_id'])
            if not aid:
                raise ValueError('Invalid candidate arXiv ID')
            if aid in seen:
                continue
            seen.add(aid)
            record = {'arxiv_id': aid, 'discovery_source': candidate.get('source', 'explicit_candidate'),
                      'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'unverified',
                      'evidence': []}
            records.append(record)
            try:
                abs_page = None
                title = candidate.get('title')
                if not title:
                    abs_page = await self.fetch(f'https://arxiv.org/abs/{aid}')
                    titles = [m.get('content', '') for m in abs_page.find_all(
                        'meta', attrs={'name': 'citation_title'})]
                    if not titles:
                        raise RetrievalError('arXiv metadata missing title')
                    title = titles[0]
                def meta(name):
                    if abs_page is None:
                        return []
                    return [m.get('content', '') for m in abs_page.find_all(
                        'meta', attrs={'name': name})]
                record['title'] = title
                html_url = f'https://arxiv.org/html/{aid}'
                html = await self.fetch(html_url)
                heading = html.select_one('h1.ltx_title')
                if not heading or normalize_title(heading.get_text(' ', strip=True)).replace(' ', '') != normalize_title(title).replace(' ', ''):
                    raise RetrievalError('arXiv HTML identity could not be confirmed')
                bibliography = html.select('.ltx_bibitem')
                if not bibliography:
                    raise RetrievalError('No structured bibliography available; not proof of no citation')
                for seed_id in graph.seed_ids:
                    seed = nodes[seed_id]
                    for entry in bibliography:
                        text = entry.get_text(' ', strip=True)
                        normalized = normalize_title(text)
                        target = normalize_title(seed.title)
                        if target and (' ' + target + ' ') in (' ' + normalized + ' '):
                            record['evidence'].append({'target_id': seed_id, 'provider': 'arxiv_html',
                                'url': html_url + ('#' + entry['id'] if entry.get('id') else ''),
                                'reference_text': text, 'match': 'normalized_full_title'})
                            break
                if not record['evidence']:
                    record['status'] = 'not_found_in_available_bibliography'
                    continue
                record['status'] = 'verified'
                doi = f'10.48550/arxiv.{aid}'
                existing = next((n for n in nodes.values() if n.arxiv_id == aid or n.doi == doi), None)
                scholar_authority = any(n.id.startswith('scholar:') for n in nodes.values())
                if scholar_authority:
                    record['status'] = 'verified' if existing else 'verified_not_in_scholar_list'
                    if existing:
                        record['paper_id'] = existing.id
                    # Evidence can corroborate Scholar membership, never extend it.
                    continue
                paper = existing
                if paper is None:
                    try:
                        paper = await provider.get_work(doi)
                        if normalize_title(paper.title) != normalize_title(title):
                            raise RetrievalError('OpenAlex/arXiv title mismatch')
                    except RetrievalError:
                        paper = None
                identifier = paper.id if paper else f'arxiv:{aid}'
                record['paper_id'] = identifier
                if identifier not in nodes and len(nodes) >= graph.config.max_papers:
                    record['status'] = 'verified_not_added_node_limit'
                    graph.truncated = True
                    graph.truncation_reasons.append('verification:max_papers')
                    continue
                if identifier not in nodes and sum(n.hop == 1 for n in nodes.values()) >= graph.config.max_papers_per_hop:
                    record['status'] = 'verified_not_added_hop_limit'
                    graph.truncated = True
                    graph.truncation_reasons.append('verification:max_papers_per_hop:1')
                    continue
                if identifier not in nodes:
                    from papergraph.models import Paper
                    if paper is None:
                        published = meta('citation_date')
                        paper = Paper(id=identifier, openalex_id=None, arxiv_id=aid, doi=doi,
                                      title=title, authors=meta('citation_author'),
                                      year=int(published[0][:4]) if published else None)
                    nodes[identifier] = PaperNode(**paper.model_dump(), hop=1, seed_ids=[], parent_ids=[])
                for evidence in record['evidence']:
                    pair = identifier, evidence['target_id']
                    if not any((e.source_id, e.target_id) == pair for e in graph.edges):
                        graph.edges.append(CitationEdge(source_id=pair[0], target_id=pair[1], provider='arxiv_html'))
            except RetrievalError as exc:
                record['error'] = str(exc)
        adjacency = defaultdict(set)
        for edge in graph.edges:
            adjacency[edge.target_id].add(edge.source_id)
        relations, paths = CitationCrawler._provenance(graph.seed_ids, adjacency, graph.config)
        for node in nodes.values():
            own = [r for r in relations if r.paper_id == node.id]
            node.hop = min(r.min_hop for r in own)
            node.seed_ids = sorted(r.seed_id for r in own)
            node.parent_ids = sorted({p for r in own for p in r.parent_ids})
        graph.nodes = sorted(nodes.values(), key=lambda n: (n.hop, n.id))
        graph.seed_relations, graph.paths = relations, paths
        graph.warnings.append('arXiv verification covers discovered or supplied candidates only; new nodes were not expanded further.')
        if any(r.paths_truncated for r in relations):
            graph.truncated = True
            graph.truncation_reasons.append('max_paths_per_pair')
        return records
