# M1 / M2 验收

用户指定用例：[LLMShare + DSE 测试报告](validation/llmshare-dse/REPORT.md)。citing 二跳共 5 个节点；both 限量扩展共 170 个节点，其中 29 篇非种子论文词面命中 DSE/Design Space Exploration。详细参数、缺失摘要与截断信息见报告；不代表语义筛选已经实现。

验证日期：2026-09-21；Python 3.11.15，httpx 0.28.1，Pydantic 2.13.5。
先完成 DESIGN.md，再实现 M1 并通过 22 项测试和四类真实输入验证，之后实现 M2。

## 离线测试

运行：`python -m pytest -q`。测试覆盖 DOI/arXiv/OpenAlex/标题、标题冒号、歧义、缺失字段、错误输入、arXiv Atom 回退、鉴权/限流错误、三方向、多 seed、多父节点、多个最短路径、环、depth 边界、cursor 分页、批量 metadata、规模限制、路径截断传播、CLI 错误码，以及真实 API 响应重放。

最终结果：`46 passed in 2.18s`。离线测试不读取个人项目配置、不请求外部 API。另已通过 editable 安装、CLI 入口与 Python 编译检查。

## M1 真实论文

[输入与输出](validation/m1-live.json)：

| 输入类型 | 输入 | 解析 ID |
| --- | --- | --- |
| DOI | 10.7717/peerj.4375 | W2741809807 |
| OpenAlex ID | W2741809807 | W2741809807（复用，无请求） |
| Title | OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts | W4288680697 |
| arXiv ID | 2309.06180 | W4386721862 |

四种输入合并为三篇论文，仅 3 次 OpenAlex 请求，arXiv DOI 直查成功，无 Atom 请求。额外查询《The state of OA》完整标题正确报重名，未默选第一条。

## M2 真实引用图

本次是刻意限量的 API 验收：depth=2，max_papers=40，max_papers_per_hop=25，max_neighbors=5，max_paths_per_pair=10。所有图均 `truncated=true`，不能解读为完整邻域。

| 场景 | Hop0 / Hop1 / Hop2 | 引用边 | 路径 | 新增 HTTP 请求 |
| --- | --- | --- | --- | --- |
| [single citing](validation/m2-single-citing.json) | 1 / 5 / 7 | 12 | 13 | 6 |
| [single references](validation/m2-single-references.json) | 1 / 5 / 19 | 30 | 26 | 5 |
| [multi both](validation/m2-multi-both.json) | 2 / 10 / 25 | 59 | 47 | 16 |

三个场景共享 provider 的运行期 metadata/response 缓存，表格显示各场景新增请求。完整验收共 31 次 OpenAlex HTTP 请求，含 M1 的 3 次和一次歧义标题检查，无重复 URL 请求。请求审计与验证时间见 [summary.json](validation/summary.json)。

真实 citing 二跳路径：

1. W4386721862 — Efficient Memory Management for Large Language Model Serving with PagedAttention
2. W4387302777 — PIT: Optimization of Dynamic Sparse Deep Learning Models via Permutation Invariant Transformation
3. W4396884968 — Making machine learning on HPC systems cost-effective and carbon-friendly

路径按发现方向 `Seed → PIT → HPC paper`，真实引用边为 `PIT cites Seed` 和 `HPC paper cites PIT`。验收脚本逐边对照 API 的 `referenced_works`，独立重算每 seed 的距离，并核对所有输出路径。

## 真实数据发现与处理

- OpenAlex 当前不支持 `sort=id:asc`，400 后修正为真实验证通过的 `publication_date:asc`，已加入契约测试与设计说明。
- PagedAttention 的所选预印本 work 有 citing 记录，但 `referenced_works=[]`。不凭空补引文；references 验收采用确有 54 条参考文献的 W2741809807。
- references 场景的 W1491139408、both 场景的 W1990832096 未被批量 API 返回。输出 warnings，未创建虚构 metadata 或悬空引用边。
- 同名预印本/正式版不能安全自动合并。标题遇到多条精确匹配时报候选 ID，DOI/ID 为明确选择入口。

[openalex-responses.json](validation/openalex-responses.json) 保存公开 API 响应的 metadata 投影，供离线回放，不保存认证 header。这是验收 fixture，不是 M3 数据库或持久缓存。

## 范围检查

数据库检查：不适用，M3 尚未开始，没有创建 SQLite 文件。Gemini 调用、PDF 下载、分类、UI 和后续 export 模块均未实现。当前 JSON 是模型结果与测试证据。自动退避、持久缓存和崩溃恢复留给后续里程碑。
