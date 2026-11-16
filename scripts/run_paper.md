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
arXiv 自动发现仅在 OpenAlex 的 citing 模式下配合关键词触发；Scholar 模式的关键词用于标注和 Gemini 分析。这些功能还要求环境网络允许相应服务。
Gemini 访问 `generativelanguage.googleapis.com`，SerpApi 访问 `serpapi.com`；
配置了密钥不代表云环境已允许这些域名。

## 常用示例

基础查询，不调用 Gemini 或自动核验：

```bash
python3 scripts/run_paper.py "2608.24650" --no-gemini --no-arxiv-auto-verify
```

关键词标注与 Gemini 分析（需要有效密钥和网络）：

```bash
python3 scripts/run_paper.py "2608.24650" --keywords "LLM serving,simulation" --gemini
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

## 缓存、刷新与继续查询

脚本默认开启 SQLite 缓存，路径为 `outputs/cache.sqlite3`。成功的 SerpApi JSON 响应缓存 1 天，其他 JSON 元数据响应及已补全论文详情缓存 7 天，Gemini 成功分析缓存 30 天；HTML/PDF 原始响应不存入 HTTP 缓存，PDF 提取出的完整论文详情可单独复用。
AI 缓存按论文标题、完整摘要、关键词与模型配置区分，内容或配置变化会重新分析。失败请求不会变成成功缓存。

```bash
# 继续同一输出目录、相同参数的上次查询
python3 scripts/run_paper.py "2608.24650" --resume
# 重新获取最新引用和详情并重做 AI 分析
python3 scripts/run_paper.py "2608.24650" --refresh
# 独立任务目录
python3 scripts/run_paper.py "2608.24650" --output-dir outputs/my-query
python3 scripts/run_paper.py "2608.24650" --output-dir outputs/my-query --resume
```

阶段记录存放在输出目录的 `.checkpoint/`。种子解析和完整引用图阶段完成后可以直接恢复；构图中途失败会重放已缓存的成功请求，再请求未完成的部分。Gemini 已保存的成功论文分析也会复用。
`--resume` 沿用原引用图检查点中的快照，不自动检查新增引用；也不代表每个网络请求都无需重发：过期或未保存的内容仍会请求。`--refresh` 优先于 `--resume`，用于显式获取新数据。
本地网站使用同一流程，可取消任务，并继续失败、取消或重启中断的任务。缓存和检查点不会随 Git 提交。

## 发表信息与版本匹配

详情优先使用 OpenAlex 中有已发表/已接收证据的期刊或会议来源。JSON 增加 `publication_status`、`publication_source`、`version_ids` 和 `metadata_match`，Excel 的 `add` 工作表也显示这些字段。
同标题候选只有共享 DOI/arXiv 标识，或作者集合和年份同时高度一致时才关联版本；否则保留歧义。PDF 页眉能补充会议和年份，但不单独证明接收状态。
列表的 `arxiv` 回退值仍不表示已经核实“未接收”；需结合 `publication_status` 和证据来源判断。
