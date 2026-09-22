"""Excel output of observed citation graphs; keywords never remove papers."""
from pathlib import Path
import math
import re
import unicodedata

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from papergraph.models import CitationGraph
from papergraph.translator import GoogleAITranslator


def write_excel(graph: CitationGraph, destination: str | Path, keywords: str = "",
                verification: list | None = None, analysis: list | None = None,
                _translated_abstracts: list[str] | None = None) -> Path:
    destination = Path(destination)
    if destination.suffix.lower() != ".xlsx":
        raise ValueError("Excel output path must end in .xlsx")
    terms = [t.strip() for t in re.split(r"[,，]", keywords) if t.strip()]
    patterns = []
    for term in terms:
        pattern = re.escape(term)
        if term.casefold() == "dse":
            pattern = r"DSE|design[\s-]+space[\s-]+exploration"
        patterns.append((term, re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)", re.I)))
    book = Workbook()
    book.remove(book.active)

    def display_width(value) -> int:
        """Approximate Excel character width, counting CJK glyphs as two."""
        if value is None:
            return 0
        return max((sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
                        for ch in line) for line in str(value).splitlines()), default=0)

    def sheet(name, headers, rows, max_widths, min_widths=None):
        ws = book.create_sheet(name)
        for row in [headers, *rows]:
            ws.append(row)
        for row in ws:
            for cell in row:
                # Metadata is literal text, never an executable Excel formula.
                if isinstance(cell.value, str):
                    cell.value = ILLEGAL_CHARACTERS_RE.sub("", cell.value)
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="24476B")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        min_widths = min_widths or [10] * len(max_widths)
        actual_widths = []
        for i, (minimum, maximum) in enumerate(zip(min_widths, max_widths), 1):
            content_width = max(display_width(ws.cell(row, i).value)
                                for row in range(1, ws.max_row + 1)) + 2
            width = min(max(content_width, minimum), maximum)
            actual_widths.append(width)
            ws.column_dimensions[get_column_letter(i)].width = width
        # openpyxl cannot request Excel AutoFit. Estimate wrapped lines so long
        # text is visible immediately in Excel/WPS without manual row resizing.
        for row_index in range(2, ws.max_row + 1):
            lines = 1
            for column, width in enumerate(actual_widths, 1):
                value = ws.cell(row_index, column).value
                if value is None:
                    continue
                wrapped = sum(max(1, math.ceil(display_width(part) / max(width - 2, 1)))
                              for part in str(value).splitlines() or [""])
                lines = max(lines, wrapped)
            ws.row_dimensions[row_index].height = min(max(22, lines * 16), 160)
        return ws

    nodes = {n.id: n for n in graph.nodes}
    analysis_by_id = {item["paper_id"]: item for item in (analysis or [])}

    # Translate abstracts if translator is provided
    abstracts_to_translate = []
    node_list = []
    for n in sorted(graph.nodes, key=lambda n: (n.hop, n.id)):
        if n.id not in graph.seed_ids:
            node_list.append(n)
            abstracts_to_translate.append(n.abstract or "")

    translated_abstracts = _translated_abstracts or [""] * len(abstracts_to_translate)

    rows = []
    add_rows = []
    for idx, n in enumerate(node_list):
        hits = [term for term, pattern in patterns if pattern.search(n.title + "\n" + (n.abstract or ""))]
        relation = ("直接引用种子" if n.hop == 1 else "二跳及以上间接关联") if graph.config.direction == "citing" else "见路径与引用边"
        hit_text = ", ".join(hits) if hits else ("未命中（不排除）" if terms else "未指定关键词")
        ai = analysis_by_id.get(n.id, {})

        abstract_en = n.abstract or ""
        abstract_zh = translated_abstracts[idx] if idx < len(translated_abstracts) else ""

        rows.append([n.title, n.year, f"{relation}（Hop {n.hop}）", abstract_en,
                     abstract_zh, hit_text, "; ".join(n.authors), ai.get("summary", ""),
                     ai.get("score"), ai.get("reason", "")])
        add_rows.append([
            n.id, n.openalex_id, n.arxiv_id, n.doi,
            f"https://doi.org/{n.doi}" if n.doi else (f"https://openalex.org/{n.openalex_id}" if n.openalex_id else ""),
            n.venue, n.cited_by_count, n.pdf_url, "有摘要" if n.abstract else "缺摘要",
            n.hop, "; ".join(n.seed_ids), "; ".join(n.parent_ids),
            ai.get("status", "未启用"), ai.get("model"),
        ])
    sheet("论文列表", ["论文标题", "年份", "与种子关系（最小Hop）", "摘要", "摘要中文翻译",
                        "辅助关键词命中", "作者", "Gemini 摘要总结", "Gemini 评分", "Gemini 评分理由"],
          rows, [68, 12, 25, 240, 240, 22, 38, 60, 16, 55],
          [28, 10, 20, 80, 80, 16, 22, 28, 14, 25])
    sheet("add", ["论文ID", "OpenAlex ID", "arXiv ID", "DOI", "论文链接", "期刊/会议",
                  "被引次数", "PDF链接", "摘要状态", "最小Hop", "来源种子", "父节点",
                  "Gemini 状态", "Gemini 模型"],
          add_rows, [30, 24, 20, 42, 55, 36, 14, 55, 16, 14, 32, 40, 28, 34])
    sheet("种子论文", ["ID", "标题", "DOI", "摘要"],
          [[s, nodes[s].title, nodes[s].doi, nodes[s].abstract] for s in graph.seed_ids], [22, 70, 45, 100])
    sheet("发现路径", ["种子ID", "论文ID", "Hop", "路径ID（从种子发现）", "路径标题"],
          [[p.seed_id, p.paper_id, p.hop, " → ".join(p.path), " → ".join(nodes[i].title for i in p.path)]
           for p in graph.paths if p.hop > 0], [22, 22, 10, 65, 110])
    sheet("引用边", ["引用方ID", "引用方标题", "被引用方ID", "被引用方标题", "关系", "证据来源"],
          [[e.source_id, nodes[e.source_id].title, e.target_id, nodes[e.target_id].title, "引用", e.provider] for e in graph.edges],
          [22, 65, 22, 65, 10, 22])
    if verification is not None:
        sheet("补充核验", ["arXiv ID", "标题", "候选来源", "核验状态", "引用证据", "核验时间", "错误"],
              [[r['arxiv_id'], r.get('title'), r['discovery_source'], r['status'],
                '\n'.join(e['url'] + '\n' + e['reference_text'] for e in r['evidence']),
                r['checked_at'], r.get('error')] for r in verification], [22, 65, 30, 35, 100, 30, 60])
    sheet("说明", ["项目", "内容"], [
        ["搜索方向", graph.config.direction], ["深度", graph.config.depth],
        ["辅助关键词", keywords], ["关键词规则", "仅匹配标题/摘要；DSE 包含 Design Space Exploration；不剔除未命中论文。"],
        ["路径方向", "发现路径从种子出发；citing 模式下与真实引用箭头相反。引用边表列明谁引用谁。"],
        ["截断", graph.truncated], ["截断原因", "; ".join(graph.truncation_reasons)],
        ["元数据警告", "\n".join(graph.warnings)],
        ["数据范围", "已观察图（来源见引用边）；补充核验仅覆盖提供的候选。缺摘要或未命中关键词不等于不相关，不保证收录完整。"],
    ], [25, 110])
    destination.parent.mkdir(parents=True, exist_ok=True)
    book.save(destination)
    return destination


async def write_excel_async(graph: CitationGraph, destination: str | Path, keywords: str = "",
                            verification: list | None = None, analysis: list | None = None,
                            translator: GoogleAITranslator | None = None) -> Path:
    """Translate abstracts when requested, then use the synchronous writer."""
    translations = None
    if translator:
        abstracts = [n.abstract or "" for n in sorted(graph.nodes, key=lambda n: (n.hop, n.id))
                     if n.id not in graph.seed_ids]
        try:
            translations = await translator.translate_batch(abstracts)
        except Exception:
            translations = ["翻译服务不可用"] * len(abstracts)
    return write_excel(graph, destination, keywords, verification, analysis, translations)
