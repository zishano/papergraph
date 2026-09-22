# `config.example.json` 字段说明

JSON 文件不能写注释，因此可直接复制 [`config.example.json`](config.example.json) 作为模板；下面列出每个字段的含义、可选值和默认行为。配置文件只保存运行参数，API Key 统一放在项目根目录的 `.env`。

| 字段 | 类型 / 可选值 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `seed` | 字符串或字符串数组 | 必填 | DOI、arXiv ID、OpenAlex ID 或完整论文标题；数组可同时指定多个种子。 |
| `source` | `openalex` / `scholar` | `openalex` | 引用关系来源。OpenAlex 支持双向图；Scholar 仅支持 `citing`。 |
| `proxy` | HTTP/HTTPS URL 或 `null` | `null` | 本次运行代理；为空时使用 `.env` 的 `PAPERGRAPH_PROXY` 或系统代理变量。 |
| `scholar_backend` | `web` / `serpapi` | `web` | `source` 为 Scholar 时选择直接网页或 SerpApi。SerpApi 需要 `.env` 中的 `SERPAPI_API_KEY`。 |
| `scholar_list` | CSV 路径或 `null` | `null` | 人工导出的 Scholar 引用列表；要求单个种子、`depth: 1`、`source: scholar`。 |
| `depth` | `0`–`3` | `2` | 引用图最大 Hop。`0` 只输出种子。 |
| `direction` | `citing` / `references` / `both` | `citing` | `citing` 查找引用种子的论文；`references` 查找种子的参考文献；`both` 同时查两者。 |
| `max_papers` | 正整数 | `1000` | 整张图最多保留的论文节点数。 |
| `max_papers_per_hop` | 正整数 | `1000` | 每个 Hop 最多加入的论文数。 |
| `max_neighbors` | 正整数 | `1000` | 每个节点、每个方向最多展开的邻居数。 |
| `max_paths_per_pair` | 正整数 | `100` | 每个种子与论文组合最多保留的发现路径数。 |
| `excel` | `.xlsx` 路径或 `null` | `null` | 指定后额外生成 Excel 工作簿。 |
| `keywords` | 逗号分隔字符串或 `null` | `null` | arXiv 候选发现和 Excel 辅助标记；不会过滤 OpenAlex 图。`DSE` 会扩展为完整短语。 |
| `arxiv_auto_verify` | `true` / `false` | `true` | 是否自动搜索 arXiv 候选并核验原文参考文献；仅用于 OpenAlex + citing + 有关键词的组合。 |
| `arxiv_max_candidates` | 正整数 | `20` | 自动核验最多处理的 arXiv 候选数。 |
| `gemini` | `true` / `false` | `true` | 是否生成中文摘要总结、评分和摘要翻译；需要 `GOOGLE_AI_API_KEY`。 |
| `gemini_model` | `auto` 或模型名 | `auto` | `auto` 自动轮换免费 Flash 模型，也可填写 `.env` 中列出的明确模型名。 |

`resolve` 只需要 `seed`；`verify` 使用 [`verify-example.json`](verify-example.json) 中的 `graph`、`candidate_arxiv`/`candidates_csv`、`output`、`excel`、`keywords`、`gemini` 和 `gemini_model` 字段。

当前自动候选模型包括：`gemini-3-flash-preview`、`gemini-3.1-flash-lite`、`gemini-3.5-flash-lite`、`gemini-3.6-flash`、`gemini-3.7-flash`、`gemini-3.8-flash`。模型可用性受 Google AI Studio 账号、地区和配额影响。
