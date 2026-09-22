# PaperGraph

PaperGraph 是一个终端工具，用于解析论文身份并构建有限深度的引用图。当前默认用 **OpenAlex 自动查找引用目标论文的后续论文**；指定关键词后，还会 **自动搜索 arXiv 候选并核验原文参考文献**。关键词同时用于 Excel 辅助标注，但不会筛掉 OpenAlex 结果。命令参数可以集中写入 JSON，终端只需指定配置文件路径。

Excel 输出还可使用 Google AI Studio 免费额度中的 Gemini Flash 模型生成中文摘要总结和 0–100 分评分。调用方式和四维评分规则参考本地 `daily-papers` 项目。论文摘要会自动翻译为中英文双语显示。

项目当前严格限定在设计文档的 M1 和 M2 范围内。详细边界见 [DESIGN.md](DESIGN.md)。

## 30 秒开始使用

项目内的 `.venv` 已经安装好。在终端运行：

```bash
cd /mnt/e/Project/LMK/papergraph
source .venv/bin/activate
# 首次使用时编辑项目根目录的 .env，填入需要的 API Key
papergraph --help
```

以后每次打开新终端，只需执行前两行。若不想激活环境，也可以直接运行 `.venv/bin/papergraph`。

以 LLMShare 为种子，查找“引用它的论文”，同时输出 JSON 和 Excel：

```bash
mkdir -p outputs
papergraph search \
  --seed 'LLMShare: Optimizing LLM Inference Serving with Hardware Architecture Exploration' \
  --direction citing \
  --depth 2 \
  --keywords DSE \
  --excel outputs/LLMShare.xlsx \
  > outputs/LLMShare.json
```

推荐使用 JSON 配置运行，命令只保留配置文件路径：

```bash
papergraph search --config configs/llmshare.json > outputs/LLMShare.json
```

示例配置见 [configs/llmshare.json](configs/llmshare.json) 和 [configs/verify-example.json](configs/verify-example.json)。JSON 中的 `proxy: null` 表示使用 `.env` 或系统代理；API Key 仍只放在 `.env`，不放进 JSON。

`citing` 的含义是：

| 层级  | 内容                     |
| ----- | ------------------------ |
| Hop 0 | LLMShare 种子论文        |
| Hop 1 | 直接引用 LLMShare 的论文 |
| Hop 2 | 引用 Hop 1 论文的论文    |

引用边统一表示 `source cites target`。发现路径按 `Seed → Hop 1 → Hop 2` 展示，所以在 `citing` 模式下，路径顺序与引用箭头方向相反。

## 从零安装或更新环境

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。运行一键脚本：

```bash
cd /mnt/e/Project/LMK/papergraph
./setup.sh
source .venv/bin/activate
```

脚本会创建 `.venv`、以 editable 模式安装程序和测试依赖，并创建 `outputs/`。更新代码后可再次运行。手动安装方式：

```bash
uv venv --python 3.11 .venv
UV_LINK_MODE=copy uv pip install --python .venv/bin/python -e '.[test]'
```

验证环境：

```bash
papergraph --help
python -m pytest -q
```

## 私密配置管理

项目根目录的 `.env` 集中保存 API Key 和本地代理配置。程序每次启动都会自动读取，不需要反复执行 `export`：

```dotenv
GOOGLE_AI_API_KEY=
OPENALEX_API_KEY=
SERPAPI_API_KEY=
PAPERGRAPH_PROXY=
PAPERGRAPH_GEMINI_MODEL=auto
```

配置步骤：

```bash
cd /mnt/e/Project/LMK/papergraph
cp -n .env.example .env
nano .env                 # 也可以直接用 VS Code 编辑
```

| 配置项                      |         是否必需 | 用途                                                                        |
| --------------------------- | ---------------: | --------------------------------------------------------------------------- |
| `GOOGLE_AI_API_KEY`       |  Gemini 功能必需 | Google AI Studio 免费额度的摘要总结、评分和摘要翻译；兼容备用变量名`GEMINI_API_KEY` |
| `OPENALEX_API_KEY`        |               否 | 提高 OpenAlex 调用额度；小规模查询通常可匿名运行                            |
| `SERPAPI_API_KEY`         | SerpApi 后端必需 | 仅用于`--source scholar --scholar-backend serpapi`                        |
| `PAPERGRAPH_PROXY`        |               否 | 本地 VPN 的 HTTP/mixed 代理，例如`http://127.0.0.1:7890`                  |
| `PAPERGRAPH_GEMINI_MODEL` |               否 | 默认`auto`；也可填写明确的 Gemini 模型名                                  |

`auto` 当前按以下顺序尝试模型：`gemini-3.6-flash`、`gemini-3.7-flash`、`gemini-3.8-flash`、`gemini-3.5-flash-lite`、`gemini-flash-lite-latest`。也可以填写 Google AI Studio 返回的其他 `generateContent` 模型名。

安全与优先级：

- `.env` 已被 `.gitignore` 排除；`.env.example` 只保存空模板，可以提交。
- 不要在 `.env.example`、README、命令行历史、JSON 或 Excel 中填写真实 Key。
- 已在终端显式设置的同名环境变量优先于 `.env`，方便临时覆盖。
- `--proxy` 优先于 `.env` 中的 `PAPERGRAPH_PROXY`；`--gemini-model` 优先于 `PAPERGRAPH_GEMINI_MODEL`。
- 项目只读取自身根目录的 `.env`，不会读取 `personal-growth` 或其他项目的配置。

## 当前支持的功能

| 功能                  |   状态 | 说明                                                                   |
| --------------------- | -----: | ---------------------------------------------------------------------- |
| 论文身份解析          | 已支持 | DOI、arXiv ID、OpenAlex ID、完整标题                                   |
| OpenAlex 自动引用图   | 已支持 | 默认`citing`；也支持 `references` 和 `both`                      |
| 有界广度优先搜索      | 已支持 | 深度 0–3、单个或多个种子、节点/分支数量限制                           |
| 路径和来源记录        | 已支持 | 去重、父节点、发现路径、引用边以及 provider                            |
| JSON 与 Excel 输出    | 已支持 | JSON 写到标准输出；Excel 包含论文、路径、边和说明                      |
| 论文去重与元数据补全  | 已支持 | 基于标题规范化去重；使用 OpenAlex 和 arXiv 补充缺失的年份、作者信息   |
| 摘要双语翻译          | 已支持 | Excel 中摘要列自动显示中英文；使用 Gemini Flash 翻译                   |
| Gemini 摘要与评分     | 已支持 | 免费 Gemini/Gemma 模型自动轮换；创新性、实用性、严谨性、清晰度四维评分 |
| 关键词辅助标注        | 已支持 | `DSE` 同时匹配 `Design Space Exploration`；不删除未命中论文        |
| arXiv 候选自动发现    | 已支持 | 根据关键词搜索近期 arXiv 标题，默认最多检查 20 篇                      |
| arXiv 原文核验        | 已支持 | 检查结构化参考文献，并自动补充验证过的直接引用边                       |
| Google Scholar 网页源 |   可选 | 可能遇到 CAPTCHA/429，不作为默认数据源                                 |
| SerpApi 接口          |   可选 | 保留接口，需要用户自己的`SERPAPI_API_KEY`                            |
| Scholar CSV 导入      |   可选 | 导入人工引用列表，适用于单种子、深度 1                                 |
| HTTP/HTTPS 代理       | 已支持 | 命令参数、项目变量或系统代理变量                                       |

## 完整命令参数

所有命令都支持 `-h` / `--help`。方括号表示可选参数；`--seed`、`--candidate-arxiv` 等 `可重复` 参数可在同一条命令中写多次。

### `papergraph resolve`

解析论文身份并输出标准化元数据 JSON。

| 参数               | 必填 | 默认值 | 说明                                                                |
| ------------------ | ---: | ------ | ------------------------------------------------------------------- |
| `--config FILE.json` | 否 | 无 | 从 JSON 读取本命令的全部参数；使用配置文件时可省略其他命令行参数 |
| `--seed VALUE`   | 条件必填 | 无     | DOI、arXiv ID、OpenAlex ID 或完整标题；可重复，用于一次解析多篇论文 |
| `-h`, `--help` |   否 | 无     | 显示帮助并退出                                                      |

示例：

```bash
papergraph resolve \
  --seed '10.1109/DAC63849.2025.11132534' \
  --seed '2607.07096'
```

### `papergraph search`

构建有限深度引用图，并可自动搜索和核验 arXiv 候选。

| 参数                                     | 必填 | 默认值       | 说明                                                                                   |
| ---------------------------------------- | ---: | ------------ | -------------------------------------------------------------------------------------- |
| `--config FILE.json`                  | 否 | 无 | 从 JSON 读取本命令的全部参数；使用配置文件时可省略其他命令行参数 |
| `--seed VALUE`                         | 条件必填 | 无           | 种子论文；支持 DOI、arXiv ID、OpenAlex ID 或完整标题；可重复                           |
| `--source {openalex,scholar}`          |   否 | `openalex` | 引用成员的数据来源；Scholar 为可选来源                                                 |
| `--proxy URL`                          |   否 | 环境变量     | 本次运行使用的 HTTP/HTTPS 代理，如`http://127.0.0.1:7890`                            |
| `--scholar-backend {web,serpapi}`      |   否 | `web`      | Scholar 使用直接网页或 SerpApi；仅在`--source scholar` 时生效                        |
| `--scholar-list FILE.csv`              |   否 | 无           | 导入 Scholar 引用列表；要求单个种子、`--depth 1` 和 `--source scholar`             |
| `--depth N`                            |   否 | `2`        | 搜索深度，允许 0–3；0 只保留种子                                                      |
| `--direction {citing,references,both}` |   否 | `citing`   | `citing` 查找引用种子的论文；`references` 查找种子的参考文献；`both` 查两个方向  |
| `--max-papers N`                       |   否 | `1000`     | 整张图允许的最大论文节点数，至少为 1                                                   |
| `--max-papers-per-hop N`               |   否 | `1000`     | 每个 Hop 允许加入的最大论文数，至少为 1                                                |
| `--max-neighbors N`                    |   否 | `1000`     | 每个节点、每个方向最多展开的邻居数，至少为 1                                           |
| `--max-paths-per-pair N`               |   否 | `100`      | 每个种子—论文组合最多保留的发现路径数，至少为 1                                       |
| `--excel FILE.xlsx`                    |   否 | 无           | 同时写出 Excel；文件名必须以`.xlsx` 结尾                                             |
| `--keywords TEXT`                      |   否 | 空           | 逗号或中文逗号分隔的辅助关键词；用于 arXiv 候选发现和 Excel 标记，不过滤 OpenAlex 节点 |
| `--arxiv-auto-verify`                  |   否 | 启用         | 自动搜索关键词相关的 arXiv 候选并核验参考文献                                          |
| `--no-arxiv-auto-verify`               |   否 | 无           | 关闭自动 arXiv 候选发现和核验                                                          |
| `--arxiv-max-candidates N`             |   否 | `20`       | 自动流程最多核验的 arXiv 候选数，至少为 1                                              |
| `--gemini`                             |   否 | 启用         | 为结果论文生成中文总结、评分和评分理由，并写入 JSON/Excel                              |
| `--no-gemini`                          |   否 | 无           | 本次运行关闭 Gemini 分析                                                               |
| `--gemini-model NAME`                  |   否 | `auto`     | 指定 Google 模型；`auto` 按免费 Flash 模型优先级自动轮换                             |
| `-h`, `--help`                       |   否 | 无           | 显示帮助并退出                                                                         |

参数组合规则：

- 自动 arXiv 核验仅在 `--source openalex`、`--direction citing` 且 `--keywords` 非空时执行。
- `--source scholar` 只支持 `--direction citing`。
- `--scholar-list` 只能与 `--source scholar` 一起使用，并要求一个种子和 `--depth 1`。
- `--scholar-backend serpapi` 需要环境变量 `SERPAPI_API_KEY`。
- Gemini 默认启用；需要 `GOOGLE_AI_API_KEY`（也兼容 `GEMINI_API_KEY`）。未设置时不会联网调用模型，JSON 和 `add` 工作表会记录 `skipped_missing_api_key`。
- JSON 始终写到标准输出。使用 `> result.json` 保存；这属于 Shell 重定向，不是 PaperGraph 参数。
- `--excel` 与 JSON 重定向可以同时使用。

完整参数示例：

```bash
papergraph search \
  --seed '完整标题' \
  --source openalex \
  --proxy http://127.0.0.1:7890 \
  --depth 2 \
  --direction citing \
  --max-papers 1000 \
  --max-papers-per-hop 1000 \
  --max-neighbors 1000 \
  --max-paths-per-pair 100 \
  --keywords 'DSE,chiplet' \
  --arxiv-auto-verify \
  --arxiv-max-candidates 20 \
  --gemini \
  --gemini-model auto \
  --excel outputs/result.xlsx \
  > outputs/result.json
```

### `papergraph verify`

对已有 `citing` 图中的额外 arXiv 候选进行原文参考文献核验。

| 参数                          |     必填 | 默认值   | 说明                                                   |
| ----------------------------- | -------: | -------- | ------------------------------------------------------ |
| `--config FILE.json`        |       否 | 无       | 从 JSON 读取本命令的全部参数                           |
| `--graph FILE.json`         | 条件必填 | 无       | 已存在的`citing` 图 JSON                             |
| `--candidate-arxiv ID`      | 条件必填 | 无       | 单个候选 arXiv ID；可重复                              |
| `--candidates-csv FILE.csv` | 条件必填 | 无       | UTF-8 CSV，必须包含`arxiv_id` 列，可选 `source` 列 |
| `--output FILE.json`        | 条件必填 | 无       | 写入补充后的图和核验审计记录                           |
| `--excel FILE.xlsx`         | 条件必填 | 无       | 写入补充后的 Excel；必须以`.xlsx` 结尾               |
| `--keywords TEXT`           |       否 | 空       | Excel 中使用的辅助关键词标记                           |
| `--gemini`                  |       否 | 启用     | 重新为图中的论文生成 Gemini 中文总结和评分             |
| `--no-gemini`               |       否 | 无       | 关闭 Gemini 分析                                       |
| `--gemini-model NAME`       |       否 | `auto` | 指定模型或使用免费模型自动轮换                         |
| `-h`, `--help`            |       否 | 无       | 显示帮助并退出                                         |

`--candidate-arxiv` 和 `--candidates-csv` 至少提供一种，也可以同时使用；重复 arXiv ID 会自动去重。

配置文件字段使用 argparse 参数名去掉连字符后的形式，例如 `--arxiv-max-candidates` 对应 `arxiv_max_candidates`。配置中的值会覆盖命令行默认值，推荐使用：

```bash
papergraph search --config configs/llmshare.json > outputs/LLMShare.json
papergraph verify --config configs/verify-example.json
```

### 解析论文与选择搜索方向

```bash
papergraph resolve --seed '完整标题或论文 ID'

# 引用种子论文的论文（默认，也是当前需求）
papergraph search --seed '完整标题' --direction citing

# 种子论文引用的参考文献
papergraph search --seed '完整标题' --direction references

# 两个方向
papergraph search --seed '完整标题' --direction both
```

运行 `papergraph search --help` 可查看深度、节点数和每节点分支数等限制参数。

### 自动发现并核验 arXiv 候选

OpenAlex 可能已经收录论文，却尚未记录其引用边。`search` 在 `citing` 模式下收到 `--keywords` 后，会自动执行以下流程：

1. 构建 OpenAlex 引用图。
2. 用关键词搜索 arXiv 候选；`DSE` 会自动扩展为 `Design Space Exploration`。
3. 逐篇检查候选的结构化参考文献。
4. 将确认引用种子的论文加入 Hop 1，并写入同一个 JSON 和 Excel。

开头的 LLMShare 命令无需提供 ThermoDSE 的 arXiv ID，即可自动补入这篇论文。默认最多检查 20 个候选，可调整：

```bash
papergraph search \
  --seed '完整标题' \
  --direction citing \
  --depth 1 \
  --keywords DSE \
  --arxiv-max-candidates 50 \
  --excel outputs/result.xlsx \
  > outputs/result.json
```

如需关闭该步骤，使用 `--no-arxiv-auto-verify`。

如果已经知道候选论文的 arXiv ID，也可以单独使用 `verify`：

```bash
papergraph verify \
  --graph outputs/LLMShare.json \
  --candidate-arxiv 2607.07096 \
  --keywords DSE \
  --output outputs/LLMShare-verified.json \
  --excel outputs/LLMShare-verified.xlsx
```

也可以传入 UTF-8 CSV：

```csv
arxiv_id,source
2607.07096,manual_candidate
```

```bash
papergraph verify \
  --graph outputs/LLMShare.json \
  --candidates-csv candidates.csv \
  --output outputs/LLMShare-verified.json \
  --excel outputs/LLMShare-verified.xlsx
```

核验器先确认 arXiv 页面身份，再在结构化 bibliography 中匹配种子完整标题。正文中只提及标题不算引用。HTML 缺失、网络失败或没有结构化参考文献都会产生明确状态，不会被解释成“没有引用”。补充边的 `provider` 为 `arxiv_html`，OpenAlex 原始边保持 `openalex`。

自动发现利用关键词缩小候选范围，再以原文参考文献作为引用证据。它不把关键词当作最终入选条件：只有参考文献完整标题匹配种子时才补边。

## 网络、API Key 和代理

### Google Gemini 摘要和评分

在 [Google AI Studio](https://aistudio.google.com/apikey) 创建 API Key，然后写入项目的 `.env`：

```dotenv
GOOGLE_AI_API_KEY=你的_key
```

`search` 和 `verify` 默认启用 Gemini。`--gemini-model auto` 会按 `daily-papers` 的方式优先尝试免费 Flash 模型，遇到模型不可用或免费额度限流时自动切换。评分总分为 100：创新性、实用性、严谨性和清晰度各 25 分。

如本次不需要模型分析：

```bash
papergraph search --seed '完整标题' --no-gemini
```

API Key 只从环境变量读取，不写入 JSON、Excel 或日志。免费额度、可用模型及调用限制由 Google 账号和地区决定。

### OpenAlex、Scholar 和代理

OpenAlex 小规模查询通常可以匿名运行。较高调用额度可在 `.env` 中配置：

```dotenv
OPENALEX_API_KEY=你的_key
```

访问失败时，可指定本地代理：

```bash
papergraph search \
  --proxy http://127.0.0.1:7890 \
  --seed '完整标题'
```

代理优先级为 `--proxy`、`.env` 中的 `PAPERGRAPH_PROXY`、`HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`：

```dotenv
PAPERGRAPH_PROXY=http://127.0.0.1:7890
```

程序不会更改系统 VPN 设置，也不会自动读取其他项目的 `.env`。

Google Scholar 只是显式选择的备用来源：

```bash
# 直接网页访问，可能被 Google 限流
papergraph search --source scholar --scholar-backend web --seed '完整标题'

# 使用 SerpApi（先在 .env 中填写 SERPAPI_API_KEY）
papergraph search --source scholar --scholar-backend serpapi --seed '完整标题'
```

Scholar 网页返回 CAPTCHA/429 时，程序会明确报错，不绕过访问限制。默认 OpenAlex 流程不需要 `SERPAPI_API_KEY`。

## 输出说明

- JSON 包含种子、论文节点、引用边、发现路径和运行参数。
- JSON 的 `analysis` 数组保存 Gemini 状态、模型、摘要总结、评分和评分理由。
- Excel 的“论文列表”只展示论文标题、年份、与种子关系（含最小 Hop）、英文摘要、中文翻译、辅助关键词命中、作者、Gemini 摘要总结、Gemini 评分和评分理由；英文摘要和中文翻译列宽均为 240。
- Excel 的 `add` 工作表保存论文 ID、OpenAlex/arXiv ID、DOI、链接、期刊会议、被引次数、PDF、来源种子、父节点以及 Gemini 调用状态和模型。
- “种子论文”“发现路径”“引用边”“说明”继续保存可追溯信息；arXiv 核验结果另有“补充核验”。
- `--keywords` 用于 arXiv 候选发现和 Excel 标记。例如 `DSE` 会扩展为完整短语；它不从 OpenAlex 图中删除未命中的论文。
- provider 字段用于区分 OpenAlex 与 arXiv 核验证据。

## 尚未实现的功能

以下属于 M3 或更后续阶段，当前没有实现：

- SQLite 持久化、跨运行缓存、运行历史、断点续跑与增量更新。
- 按年份、关键词或语义相关度自动筛选和排序；当前关键词只是 Excel 标记。
- Gemini 或其他 LLM 接入、模型轮换、批量相关性判断和深入论文分析。
- PDF 自动下载与解析，以及贡献、方法、实验和结论的结构化抽取。
- 自动主题分类、多标签归一化和研究脉络总结。
- CSV、Markdown、GraphML 等正式导出格式；当前正式输出为 JSON 和 Excel。
- Web 界面、可视化交互、向量数据库、图数据库、多智能体和云端部署。
- 不依赖关键词的完整 arXiv 反向引用索引；当前自动候选发现需要 `--keywords`，覆盖范围由关键词和候选上限决定。
- 保证 Google Scholar 网页稳定访问；访问频率、IP 和 CAPTCHA 由 Google 控制。
- 保证引用集合绝对完整；OpenAlex 自身可能延迟或缺少引用边。

## 常见问题

**结果为什么比 Google Scholar 少？**
OpenAlex 与 Scholar 的覆盖范围和更新速度不同。指定有代表性的 `--keywords` 后，程序会自动搜索并核验 arXiv 候选；仍可用 `verify` 检查额外候选。

**为什么 `DSE` 没有筛出论文？**
这是预期行为。关键词只做辅助标记，避免误删直接引用论文。

**为什么完整标题解析出错或有歧义？**
优先改用 DOI、arXiv ID 或 OpenAlex ID；这些标识比标题稳定。

**网络请求超时怎么办？**
先确认 `curl` 能访问对应站点，再设置 `--proxy` 或 `PAPERGRAPH_PROXY`。Scholar 的 429/CAPTCHA 不能仅靠重试保证解决。

## 已有验证材料

- [LLMShare 验证报告](validation/llmshare-dse/REPORT.md)
- [总体校验说明](VALIDATION.md)
- [设计文档](DESIGN.md)

LLMShare 案例已验证 OpenAlex 自动图和 arXiv 候选核验流程。验证文件保存在 `validation/llmshare-dse/`，可用于复查 JSON、Excel 和数据来源。
