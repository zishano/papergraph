# 单篇论文查询脚本

在 `/workspace/papergraph` 运行，无需激活虚拟环境：

```bash
python3 scripts/run_paper.py "https://arxiv.org/pdf/2608.24650"
```

也可以修改 `run_paper.py` 顶部的 `DEFAULT_PAPER`，之后运行 `python3 scripts/run_paper.py`。
支持完整标题、DOI、OpenAlex ID、arXiv ID 和 arXiv 链接。

## 密钥和环境配置

子进程沿用环境变量，并由 PaperGraph 自动读取项目根目录的 `.env`。
已导出的同名环境变量优先于 `.env`。不要将真实密钥写入脚本、命令行参数或提交到 Git。
如需初始化本地配置，复制 `.env.example` 为 `.env`，不要覆盖已有配置。

| 变量 | 用途 |
| --- | --- |
| `GOOGLE_AI_API_KEY` / `GEMINI_API_KEY` | Gemini 摘要、评分和翻译所需，优先使用前者 |
| `OPENALEX_API_KEY` | 可选，提高 OpenAlex 调用额度 |
| `SERPAPI_API_KEY` | 选择 Scholar 的 SerpApi 后端时需要 |
| `PAPERGRAPH_PROXY` | 可选 HTTP/mixed 代理；云环境通常直接沿用平台代理 |
| `PAPERGRAPH_GEMINI_MODEL` | 默认 `auto`，可设为可用模型名 |

默认通过 SerpApi / Google Scholar 查询两层被引论文（citing），最多 1000 个节点，每个节点最多 1000 个邻居。
Hop 1 直接引用种子，Hop 2 引用 Hop 1；SerpApi 分页会消耗查询额度。
Gemini 和 arXiv 自动核验默认开启，与原 CLI 一致；Gemini 没有密钥时会记录跳过，
arXiv 自动发现需要关键词。这些功能还要求环境网络允许相应服务。
Gemini 访问 `generativelanguage.googleapis.com`，SerpApi 访问 `serpapi.com`；
配置了密钥不代表云环境已允许这些域名。

## 常用示例

基础查询，不调用 Gemini 或自动核验：

```bash
python3 scripts/run_paper.py "2608.24650" --no-gemini --no-arxiv-auto-verify
```

关键词发现、核验及 Gemini 分析（需要有效密钥和网络）：

```bash
python3 scripts/run_paper.py "2608.24650" --keywords "LLM serving,simulation" --gemini --arxiv-auto-verify
```

扩大引用查询：

```bash
python3 scripts/run_paper.py "论文标题" --direction citing --depth 2 --max-papers 100 --max-neighbors 20
```

使用 SerpApi：

```bash
python3 scripts/run_paper.py "论文标题" --source scholar --scholar-backend serpapi --direction citing
```

其他参数包括 `--gemini-model`、`--gemini-batch-size`、`--proxy`、
`--arxiv-max-candidates`、`--max-papers-per-hop`、`--max-paths-per-pair` 和 `--scholar-list`。
查看全部参数：`python3 scripts/run_paper.py --help`。

结果保存在 `outputs/`：论文信息 `.metadata.json`、引用图 `.json` 和 `.xlsx`。
同一输入、方向和深度重复运行会更新同名结果，即使其他选项不同。
查询失败时不会替换上次成功的输出。

## 摘要补全与完整性

引用成员和边只来自 Scholar；OpenAlex 仅补充元数据，不混入引用结果。
完整摘要依次尝试 OpenAlex、结果原始页面的学术元数据、作者 PDF、arXiv 标题精确匹配及已知 DOI 的 Semantic Scholar 查询。
原始页面需标题匹配；PDF 使用 `pdftotext`（系统包 `poppler-utils`），限制 10 MiB、前两页，并要求明确的 Abstract 和 Introduction/Keywords 边界。扫描件或特殊排版可能无法提取。
各站点需要网络权限；DOI 补全需 `api.semanticscholar.org`，作者网站按实际来源放行。
无法取得完整摘要时保留论文和搜索片段，标记 snippet/missing，跳过 Gemini 评分。
JSON 的 abstract_status、abstract_source、landing_url 以及 Excel 的 add 工作表记录状态和来源。
缺少 Scholar cites_id 时无法验证下一层，输出 truncated=true 和 unverified_neighborhood，不能理解为没有后续引用。
程序不保证获取 Google Scholar 的所有记录或所有论文的完整摘要，配额、收录、权限和上限都可能限制结果。

## Scholar 与 OpenAlex 的分工

Scholar 负责引用集合与两层路径；OpenAlex 仅负责详情补全，绝不把其 referenced_works 混入 Scholar 引用图。
补全匹配依次尝试结果中的显式标识、DOI 链接、arXiv 链接对应 DOI、完整标题。为避免串论文，标识命中仍核对标题，标题多匹配时不随意选取。
第一层论文缺少 cites_id 时，解析被引链接中的 cites 参数，或按完整标题重新查询 Scholar；仅在得到唯一的被引标识时继续第二层。仍无法定位则明确标记未核验。

若只剩 Scholar 摘要片段且常规补全失败，程序会通过当前 Scholar 后端按完整标题重新搜索，检查标题一致的其他结果及资源链接，再尝试 OpenAlex 和原始页面/PDF 补全。搜索结果中的较长片段仍标记为 snippet，不用于评分；该回退会消耗 SerpApi 查询额度。
