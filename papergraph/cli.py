import os
import argparse
import asyncio
import json
import sys
import unicodedata
from pathlib import Path

import httpx

from papergraph.graph.crawler import CitationCrawler
from papergraph.gemini import GeminiScorer
from papergraph.translator import GoogleAITranslator
from papergraph.config import load_project_env
from papergraph.models import CrawlConfig
from papergraph.excel import write_excel_async
from papergraph.retrieval.scholar import ScholarProvider
from papergraph.models import CitationGraph
from papergraph.retrieval.verification import ArxivCandidateDiscovery, ArxivVerifier
from papergraph.retrieval.openalex import OpenAlexClient, RetrievalError
from papergraph.retrieval.resolver import SeedResolver


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="PaperGraph M1/M2 metadata discovery")
    commands = root.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="Resolve DOI/arXiv/OpenAlex IDs or exact titles")
    resolve.add_argument("--config", help="JSON 参数配置文件")
    resolve.add_argument("--seed", action="append")
    search = commands.add_parser("search", help="Build a bounded citation graph (M2)")
    search.add_argument("--config", help="JSON 参数配置文件")
    search.add_argument("--seed", action="append")
    search.add_argument(
        "--source",
        choices=["openalex", "scholar"],
        default="openalex",
        help="Citation source; OpenAlex is the default, Scholar remains optional",
    )
    search.add_argument("--proxy", help="HTTP proxy URL; defaults to PAPERGRAPH_PROXY or standard proxy environment")
    search.add_argument("--scholar-backend", choices=["web", "serpapi"], default="web")
    search.add_argument("--scholar-list", help="Imported Scholar citing-list CSV (single seed, depth 1)")
    search.add_argument("--depth", type=int, default=2)
    search.add_argument("--direction", choices=["citing", "references", "both"], default="citing")
    search.add_argument("--max-papers", type=int, default=1000)
    search.add_argument("--max-papers-per-hop", type=int, default=1000)
    search.add_argument("--max-neighbors", type=int, default=1000)
    search.add_argument("--max-paths-per-pair", type=int, default=100)
    search.add_argument("--excel", help="Write results to an .xlsx workbook")
    search.add_argument("--keywords", default="", help="Auxiliary annotation and arXiv candidate discovery terms; never filters papers")
    search.add_argument(
        "--arxiv-auto-verify", action=argparse.BooleanOptionalAction, default=True,
        help="Automatically discover keyword-related arXiv candidates and verify their references (default: enabled)",
    )
    search.add_argument("--arxiv-max-candidates", type=int, default=20,
                        help="Maximum arXiv keyword candidates to verify (default: 20)")
    search.add_argument("--gemini", action=argparse.BooleanOptionalAction, default=True,
                        help="Generate Gemini summaries and scores for Excel/JSON (default: enabled)")
    search.add_argument("--gemini-model", default=os.getenv("PAPERGRAPH_GEMINI_MODEL", "auto"),
                        help="Gemini model name, or auto for free-model fallback rotation")
    verify = commands.add_parser("verify", help="Verify supplied arXiv candidates against seed bibliographies")
    verify.add_argument("--config", help="JSON 参数配置文件")
    verify.add_argument("--graph", help="Existing citing graph JSON")
    verify.add_argument("--candidate-arxiv", action="append", default=[])
    verify.add_argument("--candidates-csv", help="UTF-8 CSV with arxiv_id,source columns; e.g. Scholar candidates")
    verify.add_argument("--output", help="Supplemented graph JSON, including verification audit")
    verify.add_argument("--excel")
    verify.add_argument("--keywords", default="")
    verify.add_argument("--gemini", action=argparse.BooleanOptionalAction, default=True)
    verify.add_argument("--gemini-model", default=os.getenv("PAPERGRAPH_GEMINI_MODEL", "auto"))
    return root


def clean_cli_args(arguments: list[str]) -> list[str]:
    """Remove invisible Unicode format characters introduced by copy/paste."""
    return ["".join(ch for ch in value if unicodedata.category(ch) != "Cf")
            for value in arguments]


def apply_json_config(args: argparse.Namespace) -> argparse.Namespace:
    config_path = getattr(args, "config", None)
    if not config_path:
        return args
    path = Path(config_path)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON config {path}: {exc}") from None
    if not isinstance(values, dict):
        raise ValueError("JSON config must contain an object")
    if values.get("command") and values["command"] != args.command:
        raise ValueError(f"JSON config command must be {args.command!r}")
    values.pop("command", None)
    values.pop("config", None)
    known = {action.dest for action in parser()._subparsers._group_actions[0].choices[args.command]._actions}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ValueError(f"Unknown {args.command} config fields: {', '.join(unknown)}")
    for key, value in values.items():
        setattr(args, key, value)
    if isinstance(getattr(args, "seed", None), str):
        args.seed = [args.seed]
    return args


async def run(args: argparse.Namespace) -> dict:
    args = apply_json_config(args)
    if args.command == "verify":
        import csv
        import json
        from pathlib import Path
        if not args.graph or not args.output or not args.excel:
            raise ValueError("verify requires graph, output and excel in --config or command line")
        if isinstance(getattr(args, "candidate_arxiv", None), str):
            args.candidate_arxiv = [args.candidate_arxiv]
        data = json.loads(Path(args.graph).read_text())
        graph = CitationGraph.model_validate(data)
        candidates = [{"arxiv_id": aid, "source": "explicit_candidate"} for aid in args.candidate_arxiv]
        if args.candidates_csv:
            with Path(args.candidates_csv).open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "arxiv_id" not in reader.fieldnames:
                    raise ValueError("Candidate CSV requires an arxiv_id column")
                candidates.extend({"arxiv_id": r["arxiv_id"], "source": r.get("source") or "csv_candidate"} for r in reader)
        if not candidates:
            raise ValueError("Supply --candidate-arxiv or --candidates-csv")
        if not args.excel.lower().endswith(".xlsx"):
            raise ValueError("Excel output path must end in .xlsx")
        async with httpx.AsyncClient(timeout=45) as http:
            records = await ArxivVerifier(http).verify(graph, candidates, OpenAlexClient(http))
            analysis = (await GeminiScorer(http, model=getattr(args, "gemini_model", "auto")).analyze(
                graph, args.keywords) if getattr(args, "gemini", True) else [])
        result = graph.model_dump() | {"verification": data.get("verification", []) + records,
                                       "analysis": analysis}
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        # The HTTP client must remain open while translation requests run.
        async with httpx.AsyncClient(timeout=45) as translation_http:
            translator = GoogleAITranslator(translation_http)
            await write_excel_async(graph, args.excel, args.keywords,
                                    result['verification'], analysis, translator)
        return result
    if args.command == "resolve" and not args.seed:
        raise ValueError("resolve requires seed in --config or command line")
    if args.command == "resolve":
        async with httpx.AsyncClient(timeout=30) as http:
            provider = OpenAlexClient(http)
            papers = await SeedResolver(provider).resolve_many(args.seed)
        return {"papers": [p.model_dump() for p in papers],
                "requests": {"openalex": provider.requests}}
    config = None
    if args.command == "search":
        if not args.seed:
            raise ValueError("search requires seed in --config or command line")
        config = CrawlConfig(**{k: v for k, v in vars(args).items() if k not in {
            "command", "config", "seed", "excel", "keywords", "source", "scholar_list",
            "scholar_backend", "proxy", "arxiv_auto_verify", "arxiv_max_candidates",
            "gemini", "gemini_model",
        }})
        if getattr(args, "arxiv_max_candidates", 20) < 1:
            raise ValueError("arxiv_max_candidates must be at least 1")
        if getattr(args, "excel", None) and not args.excel.lower().endswith(".xlsx"):
            raise ValueError("Excel output path must end in .xlsx")
    proxy = getattr(args, 'proxy', None) or os.getenv('PAPERGRAPH_PROXY')
    if proxy:
        from urllib.parse import urlparse
        parsed = urlparse(proxy)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('Proxy must be an http:// or https:// URL (use the VPN HTTP/mixed port)')
    async with httpx.AsyncClient(timeout=30, **({'proxy': proxy} if proxy else {})) as http:
        provider = OpenAlexClient(http)
        resolver = SeedResolver(provider)
        if config is not None and getattr(args, 'source', 'openalex') == 'scholar':
            if config.direction != 'citing':
                raise ValueError('Scholar supports citing only; choose --source openalex explicitly for other directions')
            csv_path = getattr(args, 'scholar_list', None)
            if csv_path and (len(args.seed) != 1 or config.depth != 1):
                raise ValueError('--scholar-list requires a single seed and --depth 1; no inferred second-hop list')
            scholar = ScholarProvider(http, provider, csv_path, backend=getattr(args, "scholar_backend", "web"))
            papers = [await scholar.resolve(seed) for seed in args.seed]
            graph = await CitationCrawler(scholar).crawl(papers, config)
            for edge in graph.edges:
                edge.provider = scholar.source
            graph.openalex_requests = provider.requests
            graph.warnings.extend(scholar.warnings)
            graph.warnings.append('Membership authority: Google Scholar. Imported lists are user-supplied snapshots, not live retrieval.' if csv_path else f'Membership authority: Google Scholar via {scholar.backend}; provider coverage is not exhaustive.')
            analysis = (await GeminiScorer(http, model=getattr(args, "gemini_model", "auto")).analyze(
                graph, args.keywords) if getattr(args, "gemini", True) else [])
            if getattr(args, 'excel', None):
                translator = GoogleAITranslator(http)
                await write_excel_async(graph, args.excel, args.keywords, analysis=analysis, translator=translator)
            return graph.model_dump() | {'source': scholar.source, 'analysis': analysis,
                'requests': {'scholar': scholar.requests, 'openalex': provider.requests}}
        if getattr(args, 'scholar_list', None):
            raise ValueError('--scholar-list cannot be used with --source openalex')
        papers = await resolver.resolve_many(args.seed)
        if config is not None:
            graph = await CitationCrawler(provider).crawl(papers, config)
            graph.warnings.append(
                "Citation graph source: OpenAlex. In citing mode, nodes are papers "
                "that cite the seed or the preceding hop. Missing OpenAlex citation "
                "edges may be supplemented with the arXiv verify command."
            )
            verification = []
            discovery_requests = 0
            auto_verify = (getattr(args, "arxiv_auto_verify", True)
                           and config.direction == "citing"
                           and bool(getattr(args, "keywords", "").strip()))
            if auto_verify:
                discovery = ArxivCandidateDiscovery(http)
                candidates = await discovery.search(
                    args.keywords, getattr(args, "arxiv_max_candidates", 20))
                discovery_requests = discovery.requests
                verification = await ArxivVerifier(http).verify(graph, candidates, provider)
                graph.warnings.append(
                    f"Automatic arXiv candidate verification checked {len(candidates)} keyword matches."
                )
            analysis = (await GeminiScorer(http, model=getattr(args, "gemini_model", "auto")).analyze(
                graph, getattr(args, "keywords", "")) if getattr(args, "gemini", True) else [])
            if getattr(args, "excel", None):
                translator = GoogleAITranslator(http)
                await write_excel_async(graph, args.excel, getattr(args, "keywords", ""),
                            verification if auto_verify else None, analysis, translator)
            result = graph.model_dump() | {"requests": {
                "openalex": provider.requests, "arxiv": resolver.arxiv_requests,
                "arxiv_discovery": discovery_requests,
            }}
            if auto_verify:
                result["verification"] = verification
            result["analysis"] = analysis
            return result
        return {"papers": [p.model_dump() for p in papers],
                "requests": {"openalex": provider.requests, "arxiv": resolver.arxiv_requests}}


def main() -> None:
    import json

    try:
        load_project_env()
        args = parser().parse_args(clean_cli_args(sys.argv[1:]))
        result = asyncio.run(run(args))
    except (ValueError, RetrievalError, OSError) as exc:
        print(f"papergraph: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
