from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Paper(BaseModel):
    """Provider metadata; discovery state belongs to the graph, not this object."""

    model_config = ConfigDict(extra="forbid")
    id: str
    openalex_id: str | None
    doi: str | None = None
    arxiv_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    cited_by_count: int = 0
    pdf_url: str | None = None
    referenced_works: list[str] = Field(default_factory=list)


class CitationEdge(BaseModel):
    source_id: str
    target_id: str
    relation: Literal["cites"] = "cites"
    provider: str = "openalex"


class CitationPath(BaseModel):
    seed_id: str
    paper_id: str
    path: list[str]
    hop: int


class PaperSeedRelation(BaseModel):
    seed_id: str
    paper_id: str
    min_hop: int
    parent_ids: list[str] = Field(default_factory=list)
    paths_truncated: bool = False


class PaperNode(Paper):
    hop: int
    seed_ids: list[str]
    parent_ids: list[str]


class CrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    depth: int = Field(default=2, ge=0, le=3)
    direction: Literal["citing", "references", "both"] = "citing"
    max_papers: int = Field(default=1000, ge=1)
    max_papers_per_hop: int = Field(default=1000, ge=1)
    max_neighbors: int = Field(default=1000, ge=1)
    max_paths_per_pair: int = Field(default=100, ge=1)


class CitationGraph(BaseModel):
    config: CrawlConfig
    seed_ids: list[str]
    nodes: list[PaperNode]
    edges: list[CitationEdge]
    paths: list[CitationPath]
    seed_relations: list[PaperSeedRelation]
    truncated: bool = False
    truncation_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    openalex_requests: int = 0
