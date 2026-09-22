"""Scholar-authoritative discovery, via SerpApi or a supplied citing-list CSV.

OpenAlex enrichment never contributes citation edges or controls membership.
"""
import asyncio
import time
import csv
import hashlib
import os
from pathlib import Path

import httpx

from papergraph.models import Paper
from papergraph.retrieval.scholar_web import parse_scholar_html
from papergraph.retrieval.openalex import RetrievalError
from papergraph.retrieval.resolver import normalize_title


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
            if normalize_title(existing_entry.get('title', '')) == norm_title:
                # Return existing paper, update aliases
                self.aliases['scholar:' + str(entry.get('result_id', ''))] = existing_id
                # Return cached paper
                return await self._get_cached_paper(existing_id)

        cited = (entry.get('inline_links') or {}).get('cited_by') or {}
        sid = str(cited.get('cites_id') or entry.get('result_id') or
                  hashlib.sha256((title + entry.get('link', '')).encode()).hexdigest()[:20])
        identifier = 'scholar:' + sid
        self.entries[identifier] = entry

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
            authors=authors if authors else [],
            year=year
        )

        # Metadata lookup failure cannot remove a Scholar member.
        try:
            if entry.get('paper_id'):
                candidate = await self.openalex.get_work(entry['paper_id'])
                matches = [candidate] if normalize_title(candidate.title) == norm_title else []
            else:
                matches = [p for p in await self.openalex.search_title(title)
                           if normalize_title(p.title) == norm_title]

            if len(matches) == 1:
                enriched = matches[0]
                # Merge: prefer OpenAlex metadata, but keep Scholar snippet if OpenAlex has no abstract
                paper = enriched.model_copy(update={
                    'id': identifier,
                    'referenced_works': [],
                    'abstract': enriched.abstract or snippet,
                    # Keep Scholar's basic metadata as fallback
                    'authors': enriched.authors if enriched.authors else authors,
                    'year': enriched.year if enriched.year else year,
                })
            elif len(matches) > 1:
                self.warnings.append(f'Metadata ambiguous: {identifier} ({len(matches)} OpenAlex matches); Scholar member retained with basic metadata')
            else:
                self.warnings.append(f'Metadata unresolved: {identifier}; Scholar member retained with basic metadata')
        except (ValueError, RetrievalError) as e:
            self.warnings.append(f'OpenAlex enrichment failed: {identifier} ({str(e)}); Scholar member retained with basic metadata')

        self.aliases[identifier] = identifier
        # Cache the paper for deduplication
        self.cache[identifier] = paper
        return paper

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
        cited = (entry.get('inline_links') or {}).get('cited_by') or {}
        cid = cited.get('cites_id')
        if not cid:
            self.warnings.append(f'No Scholar cites_id for {paper.id}; neighborhood not verified')
            return [], False, []
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
