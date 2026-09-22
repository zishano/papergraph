# PaperGraph Design

调研日期：2026-09-21。初始范围为 M1 + M2；用户后续明确追加要求代码输出 Excel，现单独增加 `--excel` 与辅助关键词标注，不扩展其他后续模块。

## Project Goal

从一个或多个种子论文沿真实引用边发现论文，保留可追溯的多种子、多父节点、多个路径。长期目标是关键词相关的研究演进图与综述；当前验收是 Seed → Hop1 → Hop2 的正确性。

用户当前请求授权先输出本设计，再实现 M1/M2。因此按第 49、55 节顺序执行；文档第 50 节的审核描述不增加本次用户未要求的中途审批。第 48 节的数据库检查在 M1/M2 标为不适用，数据库属于 M3；以模型与图不变量检查代替，不能借此提前实现持久化。

## Existing Project Study

当前 `/mnt/e/Project` 不是 Git 仓库；`LMK/personal-growth` 是独立既有项目，与论文图无关。检查未找到 PaperGraph、OpenAlex 或 citation crawler 实现。本项目放在 `LMK/papergraph`，不改个人成长项目，不读取其 `.env`。Python 默认是 3.9，另有 Python 3.11，项目要求 3.11+。

| 参考 | 已阅读内容 / 结论 | 本项目取舍 |
| --- | --- | --- |
| [daily-papers](https://github.com/WingEdge777/daily-papers) | 本地 `src/llm_scorer.py`、`src/models.py`、`main.py`、`config.yaml`、workflow、MIT LICENSE；`_select_best_model` 查模型并试调用，`_call_api` 处理 429/400/404/502/503/timeout，逐篇评分 | 以后借鉴 REST、模型发现、轮换；不复制硬编码候选模型、试调用额度消耗、正则挽救 JSON、把失败当 0 分、单标签分类；M1/M2 不调用 LLM |
| [AutoLitReview](https://github.com/zsjstart/AutoLitReview) | README 和 `AutoLitReview.py` 的 `search_concept`、`collect`、`analyze_local`、`relevance_filter`、`categorize_global`；MIT；OpenAlex 摘要倒排还原、分批分析、全局分类 | 借鉴流水线与 batch；不复用按标题兜底合并身份及易漂移的数组下标标识 |
| [Local Citation Network](https://github.com/LocalCitationNetwork/LocalCitationNetwork.github.io) | README、`index.js` OpenAlex wrapper、cursor、引用方向映射；GPL-3.0 | 仅借鉴算法与交互思想，不复制代码；原实现部分批量上限注释较旧，以官方为准 |
| [Citation Gecko](https://github.com/CitationGecko/gecko-react) | 旧 citation-network-explorer README 已标 deprecated，阅读替代项目 gecko-react README；MIT；多 seed、双向邻接、逐步加入 seed | 真实 seed 各自保留路径；不移植 React UI |
| [Inciteful](https://github.com/inciteful-xyz/inciteful-academic-docs/blob/master/paper-disovery-explained.md) | 官方图构建说明：二跳邻域、规模限制、重要性和相似性不同；多 seed 使用虚拟节点 | 借鉴 depth 与限制；本项目不用虚拟 seed，hop 从真实 seed 的 0 开始 |
| [PaperQA](https://github.com/Future-House/paper-qa) | README 的 search/evidence/answer 流程、元数据、可替换 provider 与证据来源设计 | 后续借鉴证据约束；不引入 Agent/RAG 框架作引用搜索底座 |
| [PyAlex](https://github.com/J535D165/pyalex) | README 与 `pyalex/api.py` Paginator、cursor、retry | 当前采用可注入 httpx AsyncClient，方便离线测试、控制请求、明确缓存；不引入同步封装 |

所有实现独立编写，不拷贝上述项目实现。后续引入第三方代码须重新确认许可证。GROBID 与 BERTopic 仅列 V2 方向。

### Current OpenAlex API / Seed Resolution

以[当前官方 API](https://help.openalex.org/api/)及[引用查询配方](https://help.openalex.org/how-to/api-recipes/)为准：

- `/works/W...` 或 `/works/https://doi.org/...` 直查；DOI 统一去前缀、解码、转小写。
- citing：`/works?filter=cites:W...`，返回引用该论文的 work；references：读取 `referenced_works`，使用 `filter=openalex:W1|W2...` 批量补 metadata（每批不超过 100）。
- `cursor=*` 后按 `meta.next_cursor` 分页，每页最多 200；不使用受 10,000 条限制的 offset 做完整遍历。参阅[官方分页源码文档](https://github.com/ourresearch/openalex-docs/blob/main/how-to-use-the-api/get-lists-of-entities/paging.md)。
- [认证文档](https://help.openalex.org/api/authentication/)目前说明允许低额度匿名请求，环境变量 `OPENALEX_API_KEY` 可增配额度；不硬编码历史定价/额度。仅发送给 OpenAlex，日志不包含密钥。实时失败显式报错，不伪造空图。
- arXiv 不是 OpenAlex 已文档化的通用外部 ID。先尝试现代 arXiv DOI `10.48550/arXiv.<id>`，404 时从[arXiv Atom API](https://info.arxiv.org/help/api/user-manual.html) `id_list` 获取标题和出版 DOI；先核对出版 DOI，再用规范化完整标题匹配，多个同名候选报歧义。兼容旧式 arXiv ID 和版本号，版本归一到 work 级。标题匹配属于 metadata 匹配，不宣称全文身份验证。
- 标题查询扫描有限候选，只有唯一规范化精确匹配才采用；没有精确匹配/存在同名时返回候选 ID，由调用者改用 DOI/ID。失败不静默选第一条。
- DOI、OpenAlex URL、arXiv URL、重复 seed 共享 run 内身份缓存。不同 OpenAlex ID 即使同名也不自动合并；预印本与正式版不强行合并，避免破坏引用关系。

## Architecture

当前：CLI → SeedResolver → OpenAlexClient → CitationCrawler → Pydantic 图结果。
长期：Citation graph → cheap filter → semantic filter → batch screening → OA/PDF → detailed analysis → classification → SQLite/cache → reports。各阶段输入输出均为结构化对象。

## Data Flow

1. 解析并规范化种子；同一 OpenAlex work 在图中只有一个节点。
2. 多源 BFS 扩展唯一 work，方向可为 citing/references/both；网络邻居获取与路径计算分离。
3. 结合已获取节点的 referenced_works 补齐节点间已知引用边，包含边界节点间边；不请求 depth 外节点。
4. 在已观察图上为每个 seed 独立 BFS，计算该 seed 的 min_hop 与所有最短父节点。枚举多个最短路径至上限，路径截断不丢 seed 关系或 min_hop。
5. CLI 输出模型 JSON 作为 M1/M2 验收数据；不是 M10 的 report/export 子系统。

## Module Breakdown / Directory Structure

```text
papergraph/
  DESIGN.md, README.md, VALIDATION.md, pyproject.toml
  papergraph/
    models.py
    retrieval/openalex.py
    retrieval/resolver.py
    graph/crawler.py
    cli.py, __main__.py
  tests/
  scripts/validate_live.py
  validation/                 # 真实验证结果，不是运行缓存
```

M3 以后再增加 storage、filtering、llm、papers、analysis、export；当前不创建占位业务模块。

## Paper Model

Paper 仅保存 metadata：id/openalex_id（W...）、doi、arxiv_id、title、authors、year、venue、abstract、cited_by_count、pdf_url、referenced_works。缺失摘要/DOI/PDF 为 null，列表用独立 default_factory。pdf_url 是元数据，不触发下载。

图中的 PaperNode 另保存 run 相关 hop（所有 seed 的最小值）、parent_ids（各 seed 最短路径父节点并集）、seed_ids。PaperSeedRelation 保存 seed_id、paper_id、min_hop、parent_ids，避免覆盖多 seed 距离。后续 analysis 不混进原始 metadata。

## Citation Edge Model

CitationEdge(source_id, target_id, relation='cites', provider='openalex')。唯一键 source+target，始终 `source cites target`。citing 扩展 u→v 的存储边是 v→u；references 为 u→v。both 可沿任一方向发现，但不能添加未经 API 数据支持的反向引用边。

## Citation Path Model

CitationPath(seed_id, paper_id, path, hop)。path 从 seed 到 paper，hop=len(path)-1；这表示遍历方向而非引文方向。本阶段保留每 seed→paper 的多个**最短**路径，不枚举环和任意非最短路径。seed 自身路径 `[seed]`。路径上限按 seed/paper 计，输出 paths_truncated 标记；所有最短父关系仍完整保留。被规模限制的图上最短距离是 sampled graph 距离，不能声称全量 citation graph 最短距离。

## Database Schema (M3+ design only)

SQLite 外键启用、WAL、schema version migrations、事务提交；不在本阶段创建数据库。

| 表 | 主键、字段与约束 |
| --- | --- |
| papers | id PK；OpenAlex ID UNIQUE；doi/arxiv_id INDEX；title/authors_json/year/venue/abstract/pdf_url/cited_by_count、created_at/updated_at |
| citations | (source_paper_id,target_paper_id,provider) PK；两个 paper FK；仅 cites |
| runs | run_id PK；status、stage、非秘密 config_json、keywords_json、created_at/updated_at |
| seeds | (run_id,seed_id) PK；run/paper FK；原始输入放 seed_inputs 表避免多 alias 覆盖 |
| seed_inputs | (run_id,input_hash) PK；seed_id FK、input_type、normalized_input |
| run_papers | (run_id,paper_id) PK；stage/status/error、next_cursor、用于恢复 |
| paper_seed_relations | (run_id,paper_id,seed_id) PK；min_hop、parent_ids_json、relevance_score |
| citation_paths | (run_id,seed_id,paper_id,path_hash) PK；path_json、hop；索引 run_id/seed_id/hop |
| keywords / paper_keywords | keyword_id PK，canonical UNIQUE；关联 paper、raw_keyword、source |
| categories / paper_categories | category_id PK；关联 (run_id,paper_id,category_id)，允许多标签 |
| analyses | (paper_id,analysis_type,input_hash,prompt_version) UNIQUE；source、evidence_json、parsed_json |
| llm_requests | id PK；provider/model、analysis_type、paper_id、input_hash、prompt_version、raw_response、parsed_response_json、status、时间 |
| crawl_tasks | (run_id,paper_id,direction) PK；cursor、status、attempt；事务内写页面与 cursor |

metadata 跨 run 共享，hop/path/筛选属于 run。恢复从已提交页面继续；崩溃前未提交请求可能重发，不宣称网络 exactly-once。

## Citation BFS Algorithm

用 ordered frontier 与全局 seen 构造有界邻域。所有 seed 先入图，逐层扩展 hop<depth 节点，每个 work 最多展开一次；同 work/direction 的邻居响应 run 内复用。即使命中已有节点也保存边，不能因 dedup 丢掉 diamond 的第二个父节点。

图构建后用边建立方向相关 adjacency；对每个 seed BFS 求 distance 与 predecessor DAG，再按距离枚举最短路径。这样 later seed 或 later parent 不需要重发 API，seed provenance 不会因全局 visited 丢失。复杂度：网络 O(展开节点分页数+reference 批数)；每 seed 距离 O(V+E)，路径枚举受 cap 约束。默认 depth=2，允许 0..3。

## Explosion Control

M2 必须有 depth、max_papers（含 seed）、max_papers_per_hop（全局新增节点）、max_neighbors（每个 work/方向）、max_paths_per_pair。参数均正整数（depth 可 0），seed 数超过 max_papers 直接拒绝。达到限制明确返回 truncation_reasons；不会把采样图说成完整图。

citing cursor 在 neighbor cap 停止；references 只补齐 cap 内 metadata，批量读取，优先复用缓存。固定 seed/ID 顺序使离线测试稳定。真实 API 验证确认不支持 `sort=id:asc`，citing 使用 `publication_date:asc`；同日顺序由服务端 cursor 决定。限制后的结果偏向早期论文，仅是可追溯样本，不是 relevance ranking。暂不做论文筛选或相关性排序。

## Filtering Strategy (M4+)

规则/年份/关键词后再语义。keyword、semantic、graph、citation 权重配置化，缺失摘要不等于无关，不单按引用数排序。V1 可用 TF-IDF/BM25 或标题摘要匹配，V2 才讨论 embedding 与主题聚类。

## Gemini Integration / LLM Provider Interface (M5+)

设计接口：`async generate_json(prompt: str, schema: type[BaseModel]) -> BaseModel` 的 Protocol；GoogleAIProvider 与业务分离。GOOGLE_AI_API_KEY 只从环境读取。运行期发现支持 generateContent 的模型，再按模式排序；不假设模型名永久有效。

JSON MIME + schema 校验。429 按冷却状态轮换，502/503/timeout 指数退避；400 区分请求/schema 问题与模型问题，不能全部当模型不可用。所有模型耗尽时保存阶段状态并退出；privacy.allow_external_llm=false 时在调度入口阻止外发。Stage A 10~20 篇批量，校验 paper ID 一一对应，解析失败逐步拆批；Stage B 一篇全文分析一个请求。

## LLM Cache / Prompt Versioning (M5+)

key 含 provider/model、analysis_type、paper_id、input_hash、prompt_version；input_hash 覆盖 keywords、seed context、citation paths、文本及 schema/settings。仅成功且验证通过的响应命中。批处理保存逐 paper 结果和 batch 请求审计，失败不缓存成成功。screening_v1 / paper_analysis_v1 / classification_v1 升级即失效旧缓存。原始响应日志须清理秘密。

## PDF Parsing (M7/M8+)

只为保留论文获取合法 OA 或本地 PDF，不绕 paywall；PyMuPDF/PyMuPDF4LLM 分节。显式 contributions 优先，其次 introduction 声明，最后推断并标 inferred。Conclusion 同样标 source，缺全文 abstract_only；提取证据与 LLM 生成分开保存。GROBID 留 V2。

## Classification (M9+)

多标签，用户类别与 Other / Emerging Topics；关键词区分 raw/canonical/source。不能把引用存在直接解释为方法继承，relationship_to_seed 需要文本证据。

## CLI

M1：`papergraph resolve --seed DOI --seed arXiv --seed W... --seed 'Title'`。
M2：`papergraph search --seed W... --depth 2 --direction citing --max-papers 100 --max-papers-per-hop 50 --max-neighbors 20`。
stdout 为模型 JSON，stderr 错误且退出码非 0。当前无 keywords 参数（未实现 M4），不提供虚假 inspect/export/resume 命令；这些随后随实际里程碑加入。用户用 shell 重定向保存验收数据。

## Milestones / MVP Scope

M0 先输出本设计。M1：四类 seed 统一解析、至少三类真实论文输入和离线异常测试，通过后再做 M2。M2：多 seed/去重/多父节点/路径、三方向、环、分页、边界和真实二跳验证；到此停止。

M3 SQLite；M4 cheap filter；M5 provider/cache；M6 batch screening；M7 PDF；M8 parser；M9 analysis/classification；M10 reports；M11 resume/robustness。以上仅设计，无提前实现。完整 V1 MVP 覆盖这条 CLI 流水线；Web UI、Agent orchestration、向量库/图数据库、登录、云部署与 BERTopic 均不在 V1。

## Risks / Validation Plan

OpenAlex 可能缺引用、摘要或将同一研究拆为多个 work；断网/限流显式失败。标题可能同名，严格匹配牺牲召回但避免错误 seed。arXiv API 可能故障，DOI 路径成功不依赖它；回退失败不伪造结果。分页和 cap 造成不完整性必须可见。引用关系本身不证明内容相关。

离线测试使用 httpx.MockTransport 与手工有向图，覆盖身份 alias、缺字段、歧义、404/鉴权、batch、cursor、diamond、多种子距离传播、cycles/both、depth=0、预算和请求计数。真实验证保存输入、时间、节点/边/path、请求统计；逐边对照原始 referenced_works。只在验证脚本保存结果，不实现长期 cache/数据库。数据库验收 N/A（M3 未开始）。

## 用户追加：多源候选核验

已增加 `verify`：已有 OpenAlex citing graph + 显式 arXiv ID / Google Scholar 候选 CSV → arXiv HTML bibliography 核验 → 补边及重算路径 → 含证据来源的 Excel。候选检索与确认引用分离；Google Scholar 自动搜索尚未接入。当前核验器不自动发现候选、不对新节点继续 BFS，不声称完整二跳覆盖；缺少 HTML 标为未核验。允许 arXiv-only ID，OpenAlex ID 可空。原始 OpenAlex referenced_works 保留不伪造，补充边单独标 provider=arxiv_html；JSON verification 保存状态、时间、参考文献文字和定位 URL。

## 正式搜索落地：Scholar authority

search 默认 source=scholar，自动入口使用 SerpApi Google Scholar adapter；SERPAPI_API_KEY 仅从环境取。CSV 单种子直接引用快照作为不依赖服务的正式入口。OpenAlex metadata 丢失保留 Scholar ID 节点，所有 metadata referenced_works 在 Scholar 流程隔离，不作为图边。arXiv 对 Scholar 图只能附核验状态，不改变名单。旧 OpenAlex 流程须显式 source=openalex。自动 adapter 已实现，当前无 key，在线 Scholar 尚未验收；CSV 流程已用真实 LLMShare 三篇名单验证。

## 默认网页联网检索

用户要求无需 key 的默认搜索：search 的 scholar-backend 默认 web，使用 Scholar HTML 的结果记录与 Cited by 链接，不通过普通关键词命中推断引用。网页查询有请求间隔和会话缓存；异常页面、验证码、限流显式失败。serpapi 保留为显式可选后端，只有该后端检查 SERPAPI_API_KEY，不自动回退到其他服务。

## 当前默认来源（2026-09-22 更新）

根据用户最终选择，`search` 默认恢复为 `source=openalex`，引用方向默认保持 `citing`。因此默认自动图回答“哪些论文引用了种子论文”，而不是“种子引用了哪些论文”。OpenAlex 是自动发现来源；arXiv metadata/HTML bibliography 用于已知候选的身份与原文引用核验，可为 OpenAlex 图增加标记为 `arxiv_html` 的证据边。Scholar 网页、SerpApi 和 Scholar CSV 均保留为显式可选来源，不参与默认流程。若 OpenAlex 缺少引用边，输出必须保留数据源限制说明，不能宣称结果完整。
