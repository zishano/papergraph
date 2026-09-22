"""User-requested M1/M2 scenario; DSE matching is test analysis, not M4.
Run from project root: PYTHONPATH=. python scripts/test_llmshare_dse.py
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import httpx

from papergraph.graph.crawler import CitationCrawler
from papergraph.excel import write_excel
from papergraph.models import CrawlConfig
from papergraph.retrieval.openalex import OpenAlexClient
from papergraph.retrieval.resolver import SeedResolver

TITLE = "LLMShare: Optimizing LLM Inference Serving with Hardware Architecture Exploration"
TERMS = {"DSE": r"\bDSE\b", "Design Space Exploration": r"\bdesign[\s-]+space[\s-]+exploration\b"}


async def main():
    output = Path("validation/llmshare-dse")
    output.mkdir(parents=True, exist_ok=True)
    urls = []
    async def audit(request):
        urls.append(str(request.url))
    async with httpx.AsyncClient(timeout=45, event_hooks={"request": [audit]}) as http:
        provider = OpenAlexClient(http)
        resolver = SeedResolver(provider)
        seed = await resolver.resolve(TITLE)
        assert seed.doi == "10.1109/dac63849.2025.11132534"
        assert (await resolver.resolve(seed.doi)).id == seed.id
        assert (await resolver.resolve(seed.id)).id == seed.id
        assert provider.requests == 1
        (output / "seed.json").write_text(seed.model_dump_json(indent=2) + "\n")
        results = []
        for direction in ["citing"]:
            before = provider.requests
            graph = await CitationCrawler(provider).crawl([seed], CrawlConfig(
                direction=direction, depth=2, max_papers=200,
                max_papers_per_hop=150, max_neighbors=25, max_paths_per_pair=20,
            ))
            (output / f"graph-{direction}.json").write_text(graph.model_dump_json(indent=2) + "\n")
            write_excel(graph, output / "LLMShare-citing-DSE.xlsx", keywords="DSE")
            nodes = {n.id: n for n in graph.nodes}
            edges = {(e.source_id, e.target_id) for e in graph.edges}
            assert len(nodes) == len(graph.nodes) and len(edges) == len(graph.edges)
            for source, target in edges:
                assert target in nodes[source].referenced_works
            for path in graph.paths:
                assert path.path[0] == seed.id and path.path[-1] == path.paper_id
                assert len(path.path) - 1 == path.hop <= 2
                for parent, child in zip(path.path, path.path[1:]):
                    assert (child, parent) in edges or (direction == "both" and (parent, child) in edges)
            matches = []
            for node in graph.nodes:
                hits = []
                for field in ["title", "abstract"]:
                    text = getattr(node, field) or ""
                    for term, pattern in TERMS.items():
                        match = re.search(pattern, text, re.I)
                        if match:
                            hits.append({"field": field, "term": term,
                                         "excerpt": text[max(0, match.start()-90):match.end()+150]})
                if hits:
                    matches.append({"id": node.id, "title": node.title, "hop": node.hop,
                                    "doi": node.doi, "hits": hits,
                                    "paths": [p.path for p in graph.paths if p.paper_id == node.id]})
            summary = {"direction": direction, "config": graph.config.model_dump(),
                       "nodes_by_hop": dict(sorted(Counter(n.hop for n in graph.nodes).items())),
                       "nodes": len(nodes), "edges": len(edges), "paths": len(graph.paths),
                       "new_requests": provider.requests - before,
                       "truncated": graph.truncated, "truncation_reasons": graph.truncation_reasons,
                       "warnings": graph.warnings,
                       "missing_abstracts": sum(n.abstract is None for n in graph.nodes),
                       "keyword_matches_including_seed": len(matches),
                       "keyword_matches_excluding_seed": sum(m['id'] != seed.id for m in matches),
                       "matches": matches}
            results.append(summary)
            print(json.dumps({k:v for k,v in summary.items() if k not in {"matches", "truncation_reasons", "warnings", "config"}},ensure_ascii=False),flush=True)
        duplicates = {u:n for u,n in Counter(urls).items() if n>1}
        assert not duplicates
        result = {"tested_at": datetime.now(timezone.utc).isoformat(), "seed": seed.id,
                  "keyword": "DSE", "expansion": "Design Space Exploration",
                  "scope": "M1/M2; post-hoc lexical test only, no filtering during BFS or LLM relevance judgement",
                  "total_requests": provider.requests, "duplicate_requests": duplicates, "cases": results}
        (output / "dse-check.json").write_text(json.dumps(result, ensure_ascii=False,indent=2)+"\n")
        lines = ["# LLMShare + DSE 测试", "", f"验证时间：{result['tested_at']}", "",
                 f"Seed：{TITLE}；OpenAlex `{seed.id}`；DOI `{seed.doi}`。",
                 "论文身份对照：[CUHK 论文记录](https://research.cuhk.edu.hk/en/publications/llmshare-optimizing-llm-inference-serving-with-hardware-architect/)。", "",
                 "范围：既有 M1/M2 实际调用。DSE 按完整单词及 Design Space Exploration（兼容空格/连字符）在返回的标题/摘要中做大小写无关匹配，仅为测试后的词面分析；未实现 M4、调用 Gemini 或下载 PDF。", "",
                 "标题、DOI、OpenAlex ID 解析到同一节点，仅 1 次解析请求。", "",
                 "| 方向 | Hop0/1/2 | 节点 | 边 | 路径 | DSE命中（不含Seed） | 缺摘要 | 截断 |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for case in results:
            counts='/'.join(str(case['nodes_by_hop'].get(h,0)) for h in range(3))
            lines.append(f"| {case['direction']} | {counts} | {case['nodes']} | {case['edges']} | {case['paths']} | {case['keyword_matches_excluding_seed']} | {case['missing_abstracts']} | {case['truncated']} |")
        lines += ["", f"完整测试 {provider.requests} 次 HTTP 请求，无重复请求；所有边已对照 referenced_works，所有路径已核验方向与 hop。", "",
                  "参数：depth=2、max_papers=200、max_papers_per_hop=150、max_neighbors=25、max_paths_per_pair=20。仅沿 citing 扩展：Hop1 直接引用 LLMShare，Hop2 引用 Hop1 论文；关键词不决定是否保留论文。", "",
                  "词面未命中不等于不相关，缺摘要尤其可能漏检。命中 DSE 缩写也可能歧义；此处不是语义筛选。citing 按发表日期升序采样，截断图不代表完整文献集合。", ""]
        for case in results:
            lines += [f"## {case['direction']}：DSE 词面命中", ""]
            for match in case['matches']:
                lines += [f"### {match['title']}", "", f"ID：`{match['id']}`；Hop：{match['hop']}；DOI：{match['doi'] or '缺失'}。", "",
                          "发现路径：`" + " → ".join(match['paths'][0]) + "`", ""]
                for hit in match['hits']:
                    lines += [f"- {hit['field']} / {hit['term']}：{hit['excerpt']}"]
                lines.append("")
            if case['warnings']:
                lines += ["元数据缺失：", ""] + [f"- {w}" for w in case['warnings']] + [""]
        lines += ["## 原始结果", "", "- [种子](seed.json)", "- [citing 图](graph-citing.json)", "- [完整词面匹配及统计](dse-check.json)"]
        (output / "REPORT.md").write_text("\n".join(lines)+"\n")

if __name__ == "__main__":
    asyncio.run(main())
