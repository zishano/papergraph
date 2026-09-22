"""Export a curated Scholar citation list as the authoritative membership list.

Metadata and evidence are read from an existing graph; missing members cause an
error rather than silently dropping a Scholar result. No automated Scholar fetch.
"""
import argparse
import csv
import json
from pathlib import Path
from openpyxl import load_workbook
from papergraph.models import CitationGraph, CitationEdge, CitationPath, PaperSeedRelation
from papergraph.excel import write_excel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True)
    parser.add_argument('--scholar-list', required=True)
    parser.add_argument('--excel', required=True)
    parser.add_argument('--keywords', default='')
    args = parser.parse_args()
    data = json.loads(Path(args.graph).read_text())
    graph = CitationGraph.model_validate(data)
    if len(graph.seed_ids) != 1:
        raise ValueError('This citation-list export requires exactly one seed')
    with Path(args.scholar_list).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    nodes = {n.id: n for n in graph.nodes}
    selected = {}
    for row in rows:
        identifier = row['paper_id']
        if identifier not in nodes:
            raise ValueError(f'Scholar member missing metadata: {identifier}; supplement it before exporting')
        selected[identifier] = row
    seed = graph.seed_ids[0]
    if seed in selected:
        raise ValueError('Seed cannot be a member of its own citing list')
    graph.nodes = [nodes[seed]] + [nodes[i].model_copy(update={'hop': 1, 'seed_ids': [seed], 'parent_ids': [seed]}) for i in selected]
    graph.edges = [CitationEdge(source_id=i, target_id=seed, provider='google_scholar_user_list') for i in selected]
    graph.paths = [CitationPath(seed_id=seed, paper_id=i, path=[seed, i] if i != seed else [seed], hop=int(i != seed)) for i in [seed, *selected]]
    graph.seed_relations = [PaperSeedRelation(seed_id=seed, paper_id=i, min_hop=int(i != seed), parent_ids=[seed] if i != seed else []) for i in [seed, *selected]]
    graph.config = graph.config.model_copy(update={'depth': 1, 'direction': 'citing'})
    graph.truncated = False
    graph.truncation_reasons = []
    graph.warnings = ['Membership follows the supplied Google Scholar list, not OpenAlex. Screenshot/list completeness and freshness are not guaranteed; no live Scholar fetch was performed.']
    write_excel(graph, args.excel, args.keywords, data.get('verification', []))
    book = load_workbook(args.excel)
    ws = book['说明']
    ws.append(['名单依据', 'Google Scholar 用户提供被引列表；OpenAlex 仅提供元数据；arXiv 核验不作为入选门槛。'])
    ws.append(['版本去重', '本次截图中 FlashGEMM 论文与同名 PDF 合并；以提供的去重名单为准。'])
    ws.append(['自动检索状态', '未自动访问 Google Scholar；此次名单依据用户截图整理。'])
    book.save(args.excel)
    book.close()
    print(f'{len(selected)} Scholar citing papers -> {args.excel}')

if __name__ == '__main__':
    main()
