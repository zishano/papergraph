"""Conservative seed resolution: never silently pick an ambiguous title."""

import asyncio
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx

from papergraph.models import Paper
from papergraph.retrieval.openalex import (
    NotFound, OpenAlexClient, RetrievalError, normalize_doi, work_id,
)


class ResolutionError(ValueError):
    pass


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", value))


def arxiv_id(value: str) -> str | None:
    value = unquote(value.strip())
    value = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", value, flags=re.I)
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    match = re.fullmatch(r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value, re.I)
    return match[1].lower() if match else None


class SeedResolver:
    def __init__(self, openalex: OpenAlexClient):
        self.openalex = openalex
        self.resolved: dict[tuple[str, str], Paper] = {}
        self.arxiv_requests = 0
        self._last_arxiv_request = 0.0

    async def resolve(self, seed: str) -> Paper:
        seed = seed.strip()
        if not seed:
            raise ResolutionError("Seed cannot be empty")
        try:
            kind, value = "openalex", work_id(seed)
        except ValueError:
            try:
                kind, value = "doi", normalize_doi(seed)
            except ValueError:
                aid = arxiv_id(seed)
                if aid:
                    kind, value = "arxiv", aid
                elif seed.lower().endswith(".pdf") or "://" in seed or seed.lower().startswith(("doi:", "arxiv:", "10.")):
                    raise ResolutionError("Unsupported or malformed seed; use DOI, arXiv ID, OpenAlex ID or full title")
                else:
                    kind, value = "title", normalize_title(seed)
        key = kind, value
        if key in self.resolved:
            return self.resolved[key]
        if kind in {"openalex", "doi"}:
            paper = await self.openalex.get_work(value)
        elif kind == "arxiv":
            paper = await self._arxiv(value)
        else:
            if not value:
                raise ResolutionError("Title must contain letters or numbers")
            paper = await self._title(seed)
        self.resolved[key] = paper
        return paper

    async def resolve_many(self, seeds: list[str]) -> list[Paper]:
        result = {}
        for seed in seeds:
            paper = await self.resolve(seed)
            result[paper.id] = paper
        return list(result.values())

    async def _title(self, title: str) -> Paper:
        candidates = await self.openalex.search_title(title)
        matches = {p.id: p for p in candidates if normalize_title(p.title) == normalize_title(title)}
        if len(matches) == 1:
            return next(iter(matches.values()))
        ids = ", ".join(p.id for p in candidates[:5]) or "none"
        reason = "Ambiguous title" if matches else "No exact title match"
        raise ResolutionError(f"{reason}; use an explicit DOI/OpenAlex ID. Candidates: {ids}")

    async def _arxiv(self, identifier: str) -> Paper:
        try:
            paper = await self.openalex.get_work(f"10.48550/arxiv.{identifier}")
        except NotFound:
            # arXiv recommends at least three seconds between API requests.
            await asyncio.sleep(max(0, 3 - (time.monotonic() - self._last_arxiv_request)))
            self._last_arxiv_request = time.monotonic()
            self.arxiv_requests += 1
            try:
                response = await self.openalex.client.get(
                    "https://export.arxiv.org/api/query", params={"id_list": identifier},
                )
                response.raise_for_status()
                root = ET.fromstring(response.text)
            except (httpx.HTTPError, ET.ParseError):
                raise RetrievalError("arXiv metadata request failed") from None
            ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
            entries = root.findall("a:entry", ns)
            if len(entries) != 1 or arxiv_id(entries[0].findtext("a:id", "", ns)) != identifier:
                raise ResolutionError("arXiv did not return the requested paper")
            entry = entries[0]
            title = " ".join(entry.findtext("a:title", "", ns).split())
            doi = entry.findtext("ar:doi", "", ns).strip()
            paper = None
            if doi:
                try:
                    candidate = await self.openalex.get_work(doi)
                    if normalize_title(candidate.title) == normalize_title(title):
                        paper = candidate
                except NotFound:
                    pass
            if paper is None:
                paper = await self._title(title)
        # Cache the alias in the provider so DOI/arXiv variants converge too.
        paper.arxiv_id = identifier
        self.openalex.aliases[f"10.48550/arxiv.{identifier}"] = paper.id
        return paper
