#!/usr/bin/env python3
"""解析一篇论文，默认通过 SerpApi 查询两层被引论文，并导出 JSON 和 Excel。

用法：python3 scripts/run_paper.py "论文标题、DOI、arXiv ID 或链接"
也可以直接修改下面的 DEFAULT_PAPER 后不带参数运行。
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse


DEFAULT_PAPER = "https://arxiv.org/pdf/2608.24650"
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", nargs="?", default=DEFAULT_PAPER)
    parser.add_argument("--direction", choices=["both", "citing", "references"], default="citing")
    parser.add_argument("--depth", type=int, choices=range(4), default=2)
    parser.add_argument("--max-papers", type=int, default=1000)
    parser.add_argument("--max-neighbors", type=int, default=1000)
    parser.add_argument("--max-papers-per-hop", type=int, default=1000)
    parser.add_argument("--max-paths-per-pair", type=int, default=100)
    parser.add_argument("--source", choices=["openalex", "scholar"], default="scholar")
    parser.add_argument("--scholar-backend", choices=["web", "serpapi"], default="serpapi")
    parser.add_argument("--scholar-list", help="人工 Scholar 引用名单 CSV 路径")
    parser.add_argument("--keywords", default="", help="关键词，例如 DSE,LLM")
    parser.add_argument("--proxy", help="覆盖 .env 中的 PAPERGRAPH_PROXY")
    parser.add_argument("--gemini", action=argparse.BooleanOptionalAction, default=True,
                        help="摘要总结和评分，默认开启；无密钥时由程序记录跳过")
    parser.add_argument("--gemini-model", help="覆盖 PAPERGRAPH_GEMINI_MODEL；未指定时使用项目配置")
    parser.add_argument("--gemini-batch-size", type=int, default=5)
    parser.add_argument("--arxiv-auto-verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--arxiv-max-candidates", type=int, default=20)
    args = parser.parse_args()
    if min(args.max_papers, args.max_neighbors, args.max_papers_per_hop,
           args.max_paths_per_pair, args.gemini_batch_size) < 1:
        parser.error("数量上限必须至少为 1")
    if args.arxiv_max_candidates < 0:
        parser.error("arxiv-max-candidates 不能为负数")
    if args.scholar_list and (args.source != "scholar" or args.depth != 1):
        parser.error("scholar-list 要求 --source scholar --depth 1")

    search_options = []
    for name in ("source", "scholar_backend", "scholar_list", "keywords", "proxy",
                 "gemini_model", "gemini_batch_size", "arxiv_max_candidates",
                 "max_papers_per_hop", "max_paths_per_pair"):
        value = getattr(args, name)
        if value is not None:
            search_options.extend(["--" + name.replace("_", "-"), str(value)])
    search_options.extend([
        "--gemini" if args.gemini else "--no-gemini",
        "--arxiv-auto-verify" if args.arxiv_auto_verify else "--no-arxiv-auto-verify",
    ])
    if args.scholar_list:
        index = search_options.index("--scholar-list") + 1
        search_options[index] = str(Path(args.scholar_list).expanduser().resolve())

    seed = args.paper.strip()
    if not seed:
        parser.error("论文名称或标识不能为空")
    url = urlparse(seed)
    if url.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        seed = re.sub(r"^/(?:pdf|abs|html)/", "", url.path)
        seed = re.sub(r"(?:\.pdf|v\d+)$", "", seed)

    cli = ROOT / ".venv/bin/papergraph"
    if not cli.is_file():
        parser.error("未找到 .venv/bin/papergraph，请先在项目目录执行 bash setup.sh")
    output = ROOT / "outputs"
    output.mkdir(exist_ok=True)
    # 标题可能含路径分隔符；散列也避免不同标题产生相同文件名。
    label = re.sub(r"[^\w.-]+", "_", seed, flags=re.UNICODE).strip("._")[:70] or "paper"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:8]
    prefix = f"{label}-{digest}-{args.direction}-d{args.depth}"

    # 失败时保留之前成功的结果，不留下空 JSON 或半成品 Excel。
    with tempfile.TemporaryDirectory(prefix="papergraph-", dir=output) as temporary:
        staging = Path(temporary)
        resolved = staging / "metadata.json"
        graph = staging / "graph.json"
        excel = staging / "graph.xlsx"
        print(f"正在处理：{seed}", flush=True)
        with resolved.open("w", encoding="utf-8") as stream:
            subprocess.run([str(cli), "resolve", "--seed", seed], cwd=ROOT, stdout=stream, check=True)
        search_seed = seed
        if args.source == "scholar":
            # Scholar 的标题匹配不支持直接用 arXiv ID 查询种子。
            search_seed = json.loads(resolved.read_text(encoding="utf-8"))["papers"][0]["title"]
        with graph.open("w", encoding="utf-8") as stream:
            subprocess.run([
                str(cli), "search", "--seed", search_seed,
                "--direction", args.direction, "--depth", str(args.depth),
                "--max-papers", str(args.max_papers), "--max-neighbors", str(args.max_neighbors),
                *search_options, "--excel", str(excel),
            ], cwd=ROOT, stdout=stream, check=True)
        data = json.loads(graph.read_text(encoding="utf-8"))
        for source, suffix in [(resolved, ".metadata.json"), (graph, ".json"), (excel, ".xlsx")]:
            destination = output / f"{prefix}{suffix}"
            source.replace(destination)
            print(destination)
        print(f"完成：{len(data['nodes'])} 个论文节点，{len(data['edges'])} 条引用边。")
        incomplete = [n for n in data['nodes'] if n.get('abstract_status') in {'snippet', 'missing'}]
        print(f"未取得完整摘要：{len(incomplete)} 篇（含种子）；详见 Excel 的 add 工作表。")
        if data.get('truncated'):
            print("引用图未完整覆盖：" + "; ".join(data.get('truncation_reasons', [])))
        print("结果受所选数据源收录范围和查询上限限制；重复查询会更新同名结果。")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
