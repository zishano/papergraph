"""Small async OpenAlex adapter with run-local metadata/request reuse."""

import os
import re
from urllib.parse import unquote

import httpx

from papergraph.models import Paper


class RetrievalError(RuntimeError):
    """A provider failed; it must not be interpreted as an empty neighborhood."""


class NotFound(RetrievalError):
    pass


def work_id(value: str) -> str:
    match = re.fullmatch(r"(?:https?://openalex\.org/)?(W\d+)", value.strip(), re.I)
    if not match:
        raise ValueError("Invalid OpenAlex work ID")
    return match[1].upper()


def normalize_doi(value: str) -> str:
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", value):
        raise ValueError("Invalid DOI")
    return value.lower()


def paper_from_work(data: dict) -> Paper:
    inverted = data.get("abstract_inverted_index") or {}
    positions = sorted((i, word) for word, indices in inverted.items() for i in indices)
    location = data.get("primary_location") or {}
    source = location.get("source") or {}
    oa_location = data.get("best_oa_location") or {}
    doi = normalize_doi(data["doi"]) if data.get("doi") else None
    arxiv_id = None
    if doi and doi.startswith("10.48550/arxiv."):
        arxiv_id = doi.removeprefix("10.48550/arxiv.")
    return Paper(
        id=work_id(data["id"]), openalex_id=work_id(data["id"]), doi=doi,
        arxiv_id=arxiv_id, title=data.get("title") or data.get("display_name") or "",
        authors=[a["author"]["display_name"] for a in data.get("authorships") or []
                 if (a.get("author") or {}).get("display_name")],
        year=data.get("publication_year"), venue=source.get("display_name"),
        abstract=" ".join(word for _, word in positions) if positions else None,
        cited_by_count=data.get("cited_by_count") or 0,
        pdf_url=oa_location.get("pdf_url") or location.get("pdf_url"),
        referenced_works=list(dict.fromkeys(work_id(w) for w in data.get("referenced_works") or [])),
    )


class OpenAlexClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None):
        self.client = client
        self.api_key = api_key if api_key is not None else os.getenv("OPENALEX_API_KEY", "")
        self.papers: dict[str, Paper] = {}
        self.aliases: dict[str, str] = {}
        self.requests = 0
        self.cache_hits = 0
        self._responses: dict[tuple, dict] = {}

    async def request(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        key = (endpoint, tuple(sorted(params.items())))
        if key in self._responses:
            self.cache_hits += 1
            return self._responses[key]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self.requests += 1
        try:
            response = await self.client.get(
                f"https://api.openalex.org/{endpoint}", params=params, headers=headers,
            )
        except httpx.RequestError:
            raise RetrievalError("OpenAlex network request failed") from None
        if response.status_code == 404:
            raise NotFound("OpenAlex work not found")
        if response.is_error:
            raise RetrievalError(f"OpenAlex HTTP {response.status_code}; check access/quota and retry")
        try:
            data = response.json()
        except ValueError:
            raise RetrievalError("OpenAlex returned invalid JSON") from None
        if not isinstance(data, dict):
            raise RetrievalError("OpenAlex response must be an object")
        self._responses[key] = data
        return data

    def remember(self, data: dict) -> Paper:
        try:
            paper = paper_from_work(data)
        except (KeyError, TypeError, ValueError):
            raise RetrievalError("OpenAlex returned invalid work metadata") from None
        if paper.id in self.papers:
            return self.papers[paper.id]
        self.papers[paper.id] = paper
        self.aliases[paper.id] = paper.id
        if paper.doi:
            self.aliases[paper.doi] = paper.id
        return paper

    async def get_work(self, identifier: str) -> Paper:
        try:
            key = work_id(identifier)
        except ValueError:
            key = normalize_doi(identifier)
        if key in self.aliases:
            self.cache_hits += 1
            return self.papers[self.aliases[key]]
        path = key if key.startswith("W") else f"https://doi.org/{key}"
        paper = self.remember(await self.request(f"works/{path}"))
        self.aliases[key] = paper.id  # Also remember redirected/merged OpenAlex IDs.
        return paper

    async def search_title(self, title: str) -> list[Paper]:
        data = await self.request("works", {"search": title, "per_page": 25})
        if not isinstance(data.get("results"), list):
            raise RetrievalError("OpenAlex search response has no results list")
        return [self.remember(w) for w in data["results"]]

    async def get_many(self, identifiers: list[str]) -> tuple[list[Paper], list[str]]:
        """Batch missing references; unavailable records are explicit, not fake nodes."""
        ids = list(dict.fromkeys(work_id(i) for i in identifiers))
        missing = [i for i in ids if i not in self.aliases]
        for start in range(0, len(missing), 100):
            batch = missing[start:start + 100]
            data = await self.request("works", {
                "filter": "openalex:" + "|".join(batch), "per_page": 100,
            })
            if not isinstance(data.get("results"), list):
                raise RetrievalError("OpenAlex batch response has no results list")
            for item in data["results"]:
                self.remember(item)
        unavailable = [i for i in ids if i not in self.aliases]
        return [self.papers[self.aliases[i]] for i in ids if i in self.aliases], unavailable

    async def neighbors(self, paper: Paper, direction: str, limit: int) -> tuple[list[Paper], bool, list[str]]:
        if limit < 1 or direction not in {"references", "citing"}:
            raise ValueError("Invalid neighbor request")
        if direction == "references":
            ids = sorted(set(paper.referenced_works))
            papers, missing = await self.get_many(ids[:limit])
            return papers, len(ids) > limit, missing
        cursor = "*"
        cursors = set()
        found: dict[str, Paper] = {}
        while cursor:
            if cursor in cursors:
                raise RetrievalError("OpenAlex repeated a pagination cursor")
            cursors.add(cursor)
            data = await self.request("works", {
                "filter": f"cites:{paper.id}", "cursor": cursor,
                "per_page": min(200, limit + 1), "sort": "publication_date:asc",
            })
            rows = data.get("results")
            if not isinstance(rows, list) or not isinstance(data.get("meta"), dict):
                raise RetrievalError("OpenAlex citation response has invalid pagination metadata")
            for item in rows:
                neighbor = self.remember(item)
                found[neighbor.id] = neighbor
                if len(found) > limit:
                    return list(found.values())[:limit], True, []
            cursor = data["meta"].get("next_cursor")
            if not rows:
                break
        return list(found.values()), False, []
