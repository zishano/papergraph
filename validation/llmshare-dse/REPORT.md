# 引用 LLMShare 的论文

## 当前正式流程（2026-09-22）

根据最终确认，默认流程为 **OpenAlex 自动发现 citing 图 + arXiv 原文参考文献核验**。Google Scholar/SerpApi 保留为可选工具，不再决定默认名单。

- [OpenAlex 自动引用图](graph-openalex.json)：Hop1 两篇、Hop2 两篇，5 条边，全部 `provider=openalex`。
- [arXiv 核验补充图](graph-openalex-arxiv-verified.json)：在 ThermoDSE 的 arXiv 参考文献中确认 LLMShare，补充 `provider=arxiv_html` 的直接引用边。
- [最终 Excel](LLMShare-OpenAlex-arXiv-verified.xlsx)：3 篇直接引用、2 篇二跳论文，包含引用边来源和补充核验表。

当前直接引用论文为 AccelStack、FlashGEMM、ThermoDSE。前两篇由 OpenAlex 自动发现；ThermoDSE 由 arXiv 原文参考文献核验补回。该组合仍可能遗漏尚未被 OpenAlex发现、又未提供 arXiv 候选的论文。

**当前名单以用户提供的 Google Scholar 被引列表截图为准。** [最新版 Excel：3 篇直接引用论文](LLMShare-Scholar.xlsx)。AccelStack、FlashGEMM、ThermoDSE 入选；FlashGEMM 同名 PDF 合并。OpenAlex 仅补 metadata，arXiv 作为证据补充，不作为入选门槛。此次没有自动访问 Google Scholar。下方为历史调研与核验记录。

## 多源核验更新

[新版 Excel：OpenAlex + arXiv 原文核验](LLMShare-verified-DSE.xlsx) · [补充图及审计](graph-verified.json)

实际执行 `papergraph verify` 后，直接引用由 OpenAlex 返回的 2 篇补充为已核实的 3 篇：AccelStack、FlashGEMM、ThermoDSE。另保留两篇二跳论文。

| 补充论文 | 候选来源 | 引用证据 | 结果 |
| --- | --- | --- | --- |
| ThermoDSE: A Thermal-Aware and Comprehensive Design Space Exploration for Chiplet-Based DNN Accelerators | 用户 Google Scholar 截图，手工整理候选 arXiv ID 2607.07096 | [arXiv 参考文献 41](https://arxiv.org/html/2607.07096#bib.bib41) | 确认直接引用 LLMShare，并命中 Design Space Exploration |

OpenAlex 已收录该论文但 referenced_works 为空；此次使用原文参考文献补边，没有把缺失边伪装成 OpenAlex 返回结果。新增候选未进一步 BFS，不能认为其二跳后继已完整检索。Google Scholar 自动检索尚未接入。

以下为此前 OpenAlex 单源测试记录，保留供对照。


[下载 Excel 表格](LLMShare-citing-DSE.xlsx)：由 `papergraph.excel.write_excel` 根据已有 citing 图生成，含 2 篇直接引用与 2 篇二跳论文；关键词未命中仍保留。

按用户澄清修正：只沿 `citing` 方向查找引用 LLMShare 的论文。DSE 仅作为辅助线索，未命中关键词的论文仍保留。本表基于 2026-09-21 已取得的 OpenAlex 结果，没有重新联网查询。

## 直接引用 LLMShare（Hop1）

| 论文 | 年份 | 主要内容（依据返回摘要概括） | 引用关系 |
| --- | --- | --- | --- |
| [AccelStack: A Cost-Driven Analysis of 3D-Stacked LLM Accelerators](https://doi.org/10.1109/iccad66269.2025.11240867) | 2025 | 对 3D 堆叠 LLM 加速器建立性能与成本模型，比较不同键合、封装和 chiplet 方案。 | AccelStack → LLMShare |
| [FlashGEMM: Mesh-Aware Efficient GEMM for 3D-Stacked LLM Accelerators](https://doi.org/10.23919/date69613.2026.11539481) | 2026 | 面向 3D 堆叠加速器的 mesh 网络优化分布式 GEMM，改善通信与计算重叠。 | FlashGEMM → LLMShare |

## 二跳间接关联（Hop2，未发现直接引用 LLMShare）

| 论文 | 年份 | 主要内容 | 引用链 |
| --- | --- | --- | --- |
| [MRAM-MoE: Efficient Inference of Mixture-of-Experts LLMs with MRAM Chiplet-based Accelerators](https://doi.org/10.1145/3787109.3815290) | 2026 | 依据摘要：采用 MRAM chiplet 加速 MoE 推理，分析 prefill/decode 性能及存储设计。 | MRAM-MoE → AccelStack → LLMShare |
| [H3-Attn](https://doi.org/10.1145/3816440.3818602) | 2026 | OpenAlex 缺摘要；仅据标题可知涉及 3D DRAM 近存处理、注意力计算及混合 head 并行。原始标题存在异常空格，完整记录保留在 JSON。 | H3-Attn → AccelStack → LLMShare |

表中箭头均表示“引用”。若只要直接引用 LLMShare 的论文，只看第一张表的两篇。

## DSE 辅助检查与范围

4 篇扩展论文的现有标题/摘要未直接出现完整单词 DSE 或 Design Space Exploration。其中 H3-Attn 缺摘要，不能因此判定其与 DSE 无关。这里不按关键词排除论文，也未进行语义相关性判定。

本次 citing 结果：1 个种子、2 篇 Hop1、2 篇 Hop2，5 条引用边；没有触发配置的规模截断。结果受 OpenAlex 收录与引用元数据完整度限制，不代表全网穷尽。

原始数据：[citing 图](graph-citing.json) · [种子](seed.json)。此前 `graph-both.json` 与 `dse-check.json` 中的 both 结果仅为历史测试记录，不属于本次用户要求的论文集合。


## 正式搜索命令验收

已通过 `papergraph search --scholar-list ... --depth 1 --excel ...` 生成本目录 graph-scholar.json 和 LLMShare-Scholar.xlsx：3 篇直接引用，所有边来源为 google_scholar_user_list。名单来自用户截图，OpenAlex 仅补 metadata。自动 SerpApi 入口已实现并测试，但本环境无 SERPAPI_API_KEY，未声称通过实时 Scholar 验证。
