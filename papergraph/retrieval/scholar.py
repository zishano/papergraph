"""Scholar-authoritative discovery, via SerpApi or a supplied citing-list CSV.

OpenAlex enrichment never contributes citation edges or controls membership.
"""
import asyncio
import time
import csv
import hashlib
import os
import re
import shutil
import subprocess
from urllib.parse import urlparse, unquote, parse_qs
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from papergraph.models import Paper
from papergraph.cache import configured_cache
from papergraph.retrieval.scholar_web import parse_scholar_html
from papergraph.retrieval.openalex import RetrievalError
from papergraph.retrieval.resolver import normalize_title
from papergraph.retrieval.resolver import arxiv_id


class ScholarProvider:
    def __init__(self, http, openalex, csv_path=None, backend="web"):
        if backend not in {"web", "serpapi"}:
            raise ValueError("Unknown Scholar backend")
        self.backend = backend
        self.last_request = 0.0
        self.http, self.openalex = http, openalex
        self.key = os.getenv('SERPAPI_API_KEY', '')
        self.csv_path = csv_path
        if not csv_path and backend == "serpapi" and not self.key:
            raise ValueError('Scholar search requires SERPAPI_API_KEY or --scholar-list CSV; no OpenAlex fallback')
        self.requests = 0
        self.aliases = {}
        self.cache = {}
        self.entries = {}
        self.warnings = []
        self.unverified_neighborhoods = set()
        self.source = 'google_scholar_user_list' if csv_path else 'google_scholar_' + backend

    async def query(self, **params):
        key = tuple(sorted(params.items()))
        if key not in self.cache:
            self.requests += 1
            if self.backend == 'web':
                await asyncio.sleep(max(0, 3 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    response = await self.http.get('https://scholar.google.com/scholar',
                        params={'hl': 'en', **params}, follow_redirects=True)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in {403, 429}:
                        raise RetrievalError(f'Google Scholar HTTP {status}: access restricted/CAPTCHA possible; proxy configuration alone cannot resolve this. Stop requests and check Scholar in your browser.') from None
                    raise RetrievalError(f'Google Scholar HTTP {status}; retrieval failed') from None
                except httpx.ProxyError:
                    raise RetrievalError('Google Scholar proxy connection failed; check --proxy or HTTPS_PROXY and the local VPN HTTP port') from None
                except httpx.TimeoutException:
                    raise RetrievalError('Google Scholar connection timed out; check proxy/VPN connectivity') from None
                except httpx.RequestError:
                    raise RetrievalError('Google Scholar network connection failed; check proxy/VPN connectivity') from None
                data = parse_scholar_html(response.text)
                self.cache[key] = data
                return data
            try:
                response = await self.http.get('https://serpapi.com/search.json', params={
                    'engine': 'google_scholar', 'api_key': self.key, **params})
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise RetrievalError('Scholar retrieval failed (SerpApi); check access/quota') from None
            if data.get('error'):
                raise RetrievalError('Scholar provider reported an error; no source fallback')
            if not isinstance(data.get('organic_results'), list):
                raise RetrievalError('Scholar response missing organic_results; cannot assume an empty list')
            self.cache[key] = data
        return self.cache[key]

    async def paper(self, entry):
        title = entry.get('title', '').strip()
        if not title:
            raise ValueError('Scholar record requires a title')

        # Normalize title for deduplication
        norm_title = normalize_title(title)

        # Check if we've already processed this paper by normalized title
        for existing_id, existing_entry in self.entries.items():
            if normalize_title(existing_entry.get('title', '')) == norm_title and self._same_scholar_work(entry, existing_entry):
                if self._cites_id(entry) and not self._cites_id(existing_entry):
                    existing_entry['inline_links'] = entry['inline_links']
                # Return existing paper, update aliases
                self.aliases['scholar:' + str(entry.get('result_id', ''))] = existing_id
                # Return cached paper
                return await self._get_cached_paper(existing_id)

        cited = (entry.get('inline_links') or {}).get('cited_by') or {}
        sid = str(cited.get('cites_id') or entry.get('result_id') or
                  hashlib.sha256((title + entry.get('link', '')).encode()).hexdigest()[:20])
        identifier = 'scholar:' + sid
        self.entries[identifier] = entry
        persistent = configured_cache()
        identity_authors = sorted({normalize_title(author['name']) for author in
                                   (entry.get('publication_info') or {}).get('authors', [])
                                   if author.get('name')})
        link = entry.get('link') or ''
        parsed_link = urlparse(link)
        # A title alone cannot identify a paper across providers or tasks. A root
        # site URL is not a paper-specific identity either.
        specific_link = bool(parsed_link.scheme in {'http', 'https'} and parsed_link.hostname
                             and (parsed_link.path.strip('/') or parsed_link.query))
        if not (specific_link or entry.get('doi') or entry.get('paper_id') or len(identity_authors) >= 2):
            persistent = None
        metadata_key = persistent.key('scholar-metadata-v3', [norm_title, link,
                                        entry.get('doi'), entry.get('paper_id'), identity_authors]) if persistent else None
        saved = persistent.get(metadata_key) if persistent and os.getenv('PAPERGRAPH_REFRESH') != '1' else None
        if saved:
            try:
                cached = Paper.model_validate(saved).model_copy(update={'id': identifier, 'referenced_works': []})
                if cached.abstract_status == 'complete':
                    self.cache[identifier] = cached
                    self.aliases[identifier] = identifier
                    return cached
            except ValueError:
                pass

        # Extract snippet from Scholar/SerpAPI as fallback abstract
        snippet = entry.get('snippet', '').strip() or None

        # Try to extract basic metadata from Scholar entry
        pub_info = entry.get('publication_info', {})
        authors = [a.get('name', '') for a in pub_info.get('authors', [])]

        # Extract year from publication_info summary (e.g., "Author et al., 2023")
        year = None
        summary = pub_info.get('summary', '')
        if summary:
            import re
            year_match = re.search(r'\b(19|20)\d{2}\b', summary)
            if year_match:
                year = int(year_match.group())

        paper = Paper(
            id=identifier,
            openalex_id=None,
            title=title,
            abstract=snippet,
            abstract_status='snippet' if snippet else 'missing',
            abstract_source='google_scholar_snippet' if snippet else None,
            landing_url=entry.get('link'),
            authors=authors if authors else [],
            year=year
        )

        # Metadata lookup failure cannot remove a Scholar member.
        try:
            matches = await self._openalex_matches(entry, title)

            if len(matches) == 1:
                enriched = matches[0]
                # Merge: prefer OpenAlex metadata, but keep Scholar snippet if OpenAlex has no abstract
                paper = enriched.model_copy(update={
                    'id': identifier,
                    'referenced_works': [],
                    'abstract': enriched.abstract or snippet,
                    'abstract_status': 'complete' if enriched.abstract and not self._abstract_incomplete(enriched.abstract) else ('snippet' if enriched.abstract or snippet else 'missing'),
                    'abstract_source': 'openalex' if enriched.abstract else ('google_scholar_snippet' if snippet else None),
                    'landing_url': entry.get('link'),
                    # Keep Scholar's basic metadata as fallback
                    'authors': enriched.authors if enriched.authors else authors,
                    'year': enriched.year if enriched.year else year,
                })
            elif len(matches) > 1:
                paper.metadata_match = 'ambiguous'
                self.warnings.append(f'Metadata ambiguous: {identifier} ({len(matches)} OpenAlex matches); Scholar member retained with basic metadata')
            else:
                self.warnings.append(f'Metadata unresolved: {identifier}; Scholar member retained with basic metadata')
        except (ValueError, RetrievalError) as e:
            self.warnings.append(f'OpenAlex enrichment failed: {identifier} ({str(e)}); Scholar member retained with basic metadata')

        if paper.abstract_status != 'complete':
            try:
                complete_abstract, abstract_source = await self._landing_metadata(paper, entry)
                if not complete_abstract:
                    complete_abstract, abstract_source = await self._complete_abstract(paper)
                if complete_abstract and not self._abstract_incomplete(complete_abstract):
                    paper = paper.model_copy(update={'abstract': complete_abstract,
                        'abstract_status': 'complete', 'abstract_source': abstract_source})
                    self.warnings.append(
                        f'Abstract enriched via {abstract_source}: {identifier}')
            except (httpx.HTTPError, ValueError):
                pass
            if paper.abstract_status != 'complete' and snippet and not self.csv_path:
                try:
                    abstract, source = await self._scholar_abstract_fallback(paper, entry)
                    if abstract:
                        paper = paper.model_copy(update={'abstract': abstract,
                            'abstract_status': 'complete', 'abstract_source': source})
                except RetrievalError:
                    self.warnings.append(f'Scholar metadata retry failed: {identifier}')
            if paper.abstract_status != 'complete':
                self.warnings.append(f'Full abstract unavailable: {identifier}; status={paper.abstract_status}; excluded from Gemini scoring')

        self.aliases[identifier] = identifier
        # Cache the paper for deduplication
        self.cache[identifier] = paper
        if persistent and paper.abstract_status == 'complete':
            persistent.put(metadata_key, paper.model_dump(), 7 * 86400)
        return paper

    async def _scholar_abstract_fallback(self, paper, original):
        """Use Scholar to find alternate sources; snippets are never full abstracts."""
        data = await self.query(q='"' + paper.title + '"', num=20)
        tried = {original.get('link')} | {r.get('link') for r in original.get('resources', [])}
        for row in data['organic_results']:
            if normalize_title(row.get('title', '')) != normalize_title(paper.title):
                continue
            pub = row.get('publication_info') or {}
            if not paper.authors:
                paper.authors = [a['name'] for a in pub.get('authors', []) if a.get('name')]
            excerpt = row.get('snippet', '').strip()
            if len(excerpt) > len(paper.abstract or ''):
                paper.abstract = excerpt
                paper.abstract_status = 'snippet'
                paper.abstract_source = 'google_scholar_snippet'
            # A fresh Scholar result may expose a DOI or arXiv version absent before.
            try:
                matches = await self._openalex_matches(row, paper.title)
            except (ValueError, RetrievalError):
                matches = []
            if len(matches) == 1:
                match = matches[0]
                for field in ('openalex_id', 'doi', 'arxiv_id', 'year', 'venue', 'pdf_url'):
                    if getattr(paper, field) is None:
                        setattr(paper, field, getattr(match, field))
                if match.publication_status in {'accepted', 'published'}:
                    paper.venue = match.venue
                    paper.publication_status = match.publication_status
                    paper.publication_source = match.publication_source
                paper.version_ids = sorted(set(paper.version_ids + match.version_ids))
                paper.metadata_match = match.metadata_match
                if match.abstract and not self._abstract_incomplete(match.abstract):
                    return match.abstract, 'openalex_via_scholar_retry'
            urls = [row.get('link')] + [r.get('link') for r in row.get('resources', [])]
            fresh = [u for u in urls if u and u not in tried]
            tried.update(fresh)
            if fresh:
                abstract, source = await self._landing_metadata(paper, {
                    'resources': [{'link': u} for u in fresh]})
                if abstract:
                    return abstract, source
        return None, None

    async def _openalex_matches(self, entry, title):
        """Resolve explicit identifiers first, then a conservative exact title match."""
        identifiers = [entry.get('paper_id'), entry.get('doi')]
        urls = [entry.get('link', '')] + [r.get('link', '') for r in entry.get('resources', [])]
        for url in urls:
            parsed = urlparse(url)
            if parsed.hostname in {'doi.org', 'dx.doi.org'}:
                identifiers.append(unquote(parsed.path.lstrip('/')))
            if parsed.hostname in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'}:
                aid = arxiv_id(url.replace('/html/', '/abs/').replace('export.arxiv.org', 'arxiv.org'))
                if aid:
                    identifiers.append('10.48550/arxiv.' + aid)
        for identifier in dict.fromkeys(i for i in identifiers if i):
            try:
                candidate = await self.openalex.get_work(identifier)
                if normalize_title(candidate.title) == normalize_title(title):
                    candidate = candidate.model_copy(update={'metadata_match': 'identifier'})
                    if candidate.publication_status == 'preprint' and len(candidate.authors) >= 2:
                        try:
                            alternatives = [p for p in await self.openalex.search_title(title)
                                            if normalize_title(p.title) == normalize_title(title)]
                            selected = self._select_versions([candidate, *alternatives])
                            if len(selected) == 1:
                                return selected
                        except (ValueError, RetrievalError):
                            pass
                    return [candidate]
            except (ValueError, RetrievalError):
                continue
        candidates = [p for p in await self.openalex.search_title(title)
                      if normalize_title(p.title) == normalize_title(title)]
        return self._select_versions(candidates)

    @staticmethod
    def _author_match(left, right):
        a = {normalize_title(name) for name in left if name}
        b = {normalize_title(name) for name in right if name}
        return len(a & b) >= 2 and len(a & b) / len(a | b) >= 0.8

    @classmethod
    def _select_versions(cls, candidates):
        """Prefer the published edition only when all candidates share identity evidence."""
        unique = {p.id: p for p in candidates}
        candidates = list(unique.values())
        if len(candidates) == 1:
            return [candidates[0].model_copy(update={'metadata_match': candidates[0].metadata_match or 'exact_title'})]
        if not candidates:
            return []
        for index, left in enumerate(candidates):
            for right in candidates[index + 1:]:
                identifier_match = bool((left.doi and right.doi and left.doi.casefold() == right.doi.casefold())
                                        or (left.arxiv_id and left.arxiv_id == right.arxiv_id))
                author_match = cls._author_match(left.authors, right.authors)
                years_close = left.year is not None and right.year is not None and abs(left.year - right.year) <= 2
                if not identifier_match and not (author_match and years_close):
                    return candidates
        rank = {'unknown': 0, 'preprint': 1, 'accepted': 2, 'published': 3}
        selected = max(candidates, key=lambda p: (rank[p.publication_status], bool(p.abstract), p.id))
        identifiers = sorted({value for p in candidates for value in
                              [p.openalex_id, p.doi, ('arxiv:' + p.arxiv_id) if p.arxiv_id else None, *p.version_ids] if value})
        return [selected.model_copy(update={'version_ids': identifiers, 'metadata_match': 'matched_versions'})]

    @classmethod
    def _same_scholar_work(cls, left, right):
        if left.get('result_id') and left.get('result_id') == right.get('result_id'):
            return True
        if cls._cites_id(left) and cls._cites_id(left) == cls._cites_id(right):
            return True
        for field in ('doi', 'paper_id', 'link'):
            if left.get(field) and left.get(field) == right.get(field):
                return True
        authors = lambda row: [a.get('name') for a in (row.get('publication_info') or {}).get('authors', [])]
        return cls._author_match(authors(left), authors(right))

    @staticmethod
    def _cites_id(entry):
        cited = (entry.get('inline_links') or {}).get('cited_by') or {}
        if cited.get('cites_id'):
            return str(cited['cites_id'])
        for key in ('link', 'serpapi_scholar_link'):
            value = parse_qs(urlparse(cited.get(key, '')).query).get('cites')
            if value:
                return value[0]
        return None

    async def _recover_cites_id(self, paper):
        """Re-find the same paper; never substitute an OpenAlex citation list."""
        data = await self.query(q='"' + paper.title + '"', num=20)
        matches = [r for r in data['organic_results']
                   if normalize_title(r.get('title', '')) == normalize_title(paper.title)]
        ids = {self._cites_id(r) for r in matches if self._cites_id(r)}
        if len(ids) == 1:
            cid = ids.pop()
            self.entries[paper.id]['inline_links'] = {
                **(self.entries[paper.id].get('inline_links') or {}),
                'cited_by': {'cites_id': cid},
            }
            return cid
        return None

    @staticmethod
    def _abstract_incomplete(abstract):
        if not abstract or not abstract.strip():
            return True
        return '...' in abstract or '…' in abstract

    async def _landing_metadata(self, paper, entry):
        """Read explicit scholarly metadata, never infer an abstract from page prose."""
        urls = [entry.get('link')] + [r.get('link') for r in entry.get('resources', [])]
        for url in dict.fromkeys(u for u in urls if u):
            parsed = urlparse(url)
            if parsed.scheme != 'https':
                continue
            if parsed.hostname in {'arxiv.org', 'export.arxiv.org', 'www.arxiv.org'}:
                url = re.sub(r'/(pdf|html)/', '/abs/', url)
                url = re.sub(r'\.pdf$', '', url)
            elif parsed.path.lower().endswith('.pdf'):
                sources = [url]
                # GitHub Pages' public source repository can provide the same PDF.
                if parsed.hostname and parsed.hostname.endswith('.github.io'):
                    owner = parsed.hostname.removesuffix('.github.io')
                    if re.fullmatch(r'[A-Za-z0-9-]+', owner):
                        for branch in ('master', 'main'):
                            sources.append(f'https://raw.githubusercontent.com/{owner}/{owner}.github.io/{branch}{parsed.path}')
                for source in sources:
                    abstract = await self._pdf_abstract(source, paper.title, paper=paper)
                    if abstract:
                        paper.pdf_url = source
                        return abstract, source
                continue
            try:
                response = await self.http.get(url, follow_redirects=True)
                response.raise_for_status()
                html = BeautifulSoup(response.text, 'html.parser')
                def meta(*names):
                    for name in names:
                        node = html.find('meta', attrs={'name': name})
                        if node and node.get('content'):
                            return node['content'].strip()
                    return None
                title = meta('citation_title', 'dc.title', 'DC.title')
                if not title or normalize_title(title) != normalize_title(paper.title):
                    continue
                abstract = meta('citation_abstract', 'dc.description', 'DC.description', 'DCTERMS.abstract')
                if not abstract:
                    node = html.select_one('blockquote.abstract')
                    if node:
                        abstract = re.sub(r'^Abstract:\s*', '', node.get_text(' ', strip=True))
                if not paper.authors:
                    paper.authors = [n['content'] for n in html.select('meta[name="citation_author"][content]')]
                if not paper.doi:
                    paper.doi = meta('citation_doi')
                if not paper.year:
                    date = meta('citation_publication_date', 'citation_date') or ''
                    match = re.search(r'\b(?:19|20)\d{2}\b', date)
                    if match:
                        paper.year = int(match[0])
                paper.pdf_url = paper.pdf_url or meta('citation_pdf_url')
                published_venue = meta('citation_journal_title', 'citation_conference_title')
                if published_venue and 'arxiv' not in published_venue.casefold():
                    paper.venue = published_venue
                    paper.publication_source = url
                    if meta('citation_publication_date'):
                        paper.publication_status = 'published'
                if abstract and not self._abstract_incomplete(abstract):
                    return abstract, url
            except httpx.HTTPError:
                continue
        return None, None

    async def _pdf_abstract(self, url, title, paper=None):
        if not shutil.which('pdftotext'):
            self.warnings.append('PDF extraction unavailable: install poppler-utils')
            return None
        try:
            chunks, size = [], 0
            async with self.http.stream('GET', url, follow_redirects=True) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 10 * 1024 * 1024:
                        return None
                    chunks.append(chunk)
            content = b''.join(chunks)
            if not content.startswith(b'%PDF-'):
                return None
            result = await asyncio.to_thread(subprocess.run,
                ['pdftotext', '-raw', '-f', '1', '-l', '2', '-', '-'], input=content,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
            text = result.stdout.decode('utf-8', errors='replace')
            if normalize_title(title) not in normalize_title(text):
                return None
            if paper is not None:
                # Use publication headers, never arbitrary years in the abstract/body.
                date = re.search(r'(?mi)^([^\n]{0,100}@[^\n]{0,80}\b((?:19|20)\d{2})\b[^\n]*|(?:©|Copyright)\s*((?:19|20)\d{2})\b[^\n]*)$', text)
                if date:
                    paper.year = paper.year or int(date[2] or date[3])
                    if (not paper.venue or 'arxiv' in paper.venue.casefold()) and date[2]:
                        paper.venue = date[1].strip()
                        paper.publication_source = url
                        paper.publication_status = 'unknown'  # Header names a venue, not a formal acceptance decision.
            match = re.search(r'\bAbstract\s*[:—–-]?\s*(.+?)\n\s*(?:(?:\d+[. ]*)?Introduction\b|(?:CCS Concepts|Keywords|Index Terms)\b)', text, re.S | re.I)
            if match:
                abstract = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', match[1])
                abstract = ' '.join(abstract.split())
                if len(abstract) >= 80 and not self._abstract_incomplete(abstract):
                    return abstract
        except (httpx.HTTPError, OSError, subprocess.SubprocessError):
            pass
        return None

    async def _complete_abstract(self, paper):
        """Enrich metadata only; Scholar remains authoritative for graph membership."""
        try:
            response = await self.http.get('https://arxiv.org/search/', params={
                'query': paper.title,
                'searchtype': 'title',
                'abstracts': 'show',
                'order': '-announced_date_first',
                'size': 25,
            }, follow_redirects=True, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PaperGraph/0.1; metadata-enrichment)',
                'Accept': 'text/html,application/xhtml+xml',
            })
            response.raise_for_status()
            html = BeautifulSoup(response.text, 'html.parser')
            target = normalize_title(paper.title).replace(' ', '')
            for item in html.select('li.arxiv-result'):
                title_node = item.select_one('p.title')
                abstract_node = item.select_one('span.abstract-full')
                if not title_node or not abstract_node:
                    continue
                candidate = normalize_title(title_node.get_text(' ', strip=True)).replace(' ', '')
                if candidate == target:
                    abstract = abstract_node.get_text(' ', strip=True)
                    abstract = re.sub(r'\s*(?:△\s*Less|▽\s*More)\s*$', '', abstract).strip()
                    if abstract:
                        return abstract, 'arxiv'
        except httpx.HTTPError:
            pass

        if paper.doi:
            response = await self.http.get(
                f'https://api.semanticscholar.org/graph/v1/paper/DOI:{paper.doi}',
                params={'fields': 'title,abstract'},
            )
            response.raise_for_status()
            data = response.json()
            abstract = str(data.get('abstract') or '').strip()
            if abstract and normalize_title(data.get('title', '')) == normalize_title(paper.title):
                return abstract, 'semantic_scholar_doi'
        return None, None

    async def _get_cached_paper(self, identifier):
        """Retrieve cached paper by identifier."""
        if identifier in self.cache:
            return self.cache[identifier]
        # Reconstruct from entries if not cached
        entry = self.entries.get(identifier)
        if entry:
            snippet = entry.get('snippet', '').strip() or None
            return Paper(id=identifier, openalex_id=None, title=entry.get('title', ''), abstract=snippet)
        raise ValueError(f"Paper {identifier} not found in cache or entries")

    async def resolve(self, title):
        if self.csv_path:
            return await self.paper({'title': title, 'result_id': 'seed-' + hashlib.sha256(title.encode()).hexdigest()[:16]})
        data = await self.query(q=title, num=20)
        matches = [r for r in data['organic_results'] if normalize_title(r.get('title', '')) == normalize_title(title)]
        if len(matches) != 1:
            raise ValueError('Scholar seed title is missing or ambiguous; supply its exact title')
        return await self.paper(matches[0])

    async def neighbors(self, paper, direction, limit):
        if direction != 'citing':
            raise ValueError('Scholar supports citing only')
        if self.csv_path:
            with Path(self.csv_path).open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            if any(not row.get('title') for row in rows):
                raise ValueError('Scholar CSV requires title for every row')
            unique = {}
            for row in rows:
                key = row.get('paper_id') or normalize_title(row['title'])
                unique.setdefault(key, row)
            result = [await self.paper(dict(row, result_id=row.get('paper_id') or normalize_title(row['title'])))
                      for row in list(unique.values())[:limit]]
            return result, len(unique) > limit, []
        entry = self.entries[paper.id]
        cid = self._cites_id(entry)
        if not cid:
            cid = await self._recover_cites_id(paper)
        if not cid:
            self.unverified_neighborhoods.add(paper.id)
            self.warnings.append(f'No Scholar cites_id for {paper.id}; neighborhood not verified')
            return [], True, []
        rows, seen, start = [], set(), 0
        while True:
            data = await self.query(cites=str(cid), start=start, num=20)
            page = data['organic_results']
            for row in page:
                key = str(((row.get('inline_links') or {}).get('cited_by') or {}).get('cites_id') or row.get('result_id') or (row.get('title'), row.get('link')))
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
            more = bool((data.get('pagination') or data.get('serpapi_pagination') or {}).get('next'))
            if len(rows) > limit or not more:
                break
            if not page:
                raise RetrievalError('Scholar pagination returned an empty page with next link')
            next_start = data.get('next_start')
            if next_start is not None and next_start <= start:
                raise RetrievalError('Google Scholar pagination did not advance')
            start = next_start if next_start is not None else start + 20
            if start >= 1000:
                self.warnings.append('Scholar paging stopped at 1000 raw results')
                more = True
                break
        return [await self.paper(r) for r in rows[:limit]], len(rows) > limit or more, []
