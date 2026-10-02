# NH₃-SCR 催化剂文献数据工作台

本项目从 NH₃-SCR 原始研究中整理催化剂、反应条件、性能与表征数据。程序先生成可追溯到原文的候选记录，经人工核验后导出；目标是为后续机器学习寻找关键变量和更优催化剂/条件提供数据。

**当前处于阶段 1.2：多论文数据提取测试，尚未开始机器学习建模。**截至 2026-10-01，工作台 0.8 已接通统一在线检索、摘要核对、按出版社接口批量获取正文、浏览器页面采集、图中读数、原文审核和导出。请先看[一页项目进度](docs/PROJECT_STATUS_2026-10-01.md)：其中分清了软件验收、真实论文候选和能够建模的数据。

| 环节 | 已实现 | 仍需完成 |
| --- | --- | --- |
| 论文初筛 | 摘要导入、DOI 保留、可调整权重的阅读排序、人工确认 | 用人工标注集评估排序 |
| 数据获取与提取 | PDF/HTML/表格规则提取、常见单位归一、人工图中取点、语义候选 | 提升跨论文覆盖，关联跨页条件与样品 |
| 数据质量 | 每条数值显示核对问题、原文和 PDF 页；审核与历史自动保存 | 用更多原始研究测量错提和漏提 |
| 建模 | 已规划变量筛选与性能预测 | 数据质量达标后比较模型和可解释公式 |

仓库只保留源码、少量示例、测试代码和精简说明；真实论文、自动生成的测试目录、审核项目和 EXE 留在本地。[GitHub 分享范围与上传步骤](docs/GITHUB_SHARE.md)说明了提交内容。

## 文献数据工作台 EXE 0.8（推荐入口）

公开交付包为 `NH3SCR_Workbench_0.8_Windows.zip`，解压后双击 `NH3SCR_Workbench.exe`，无需安装 Python；包中还包含 Chrome 插件和操作说明，只内置两条合成演示摘要。公开版构建目录为 `artifact_work/NH3SCR_Workbench_0.8_Public/`；原 `artifact_work/NH3SCR_Workbench_0.8/` 是含本地论文摘要的内部版本。EXE 作为 Release 附件，不放入源码提交。从 GitHub 克隆后，可按下文安装依赖并运行源码，再导入自己的摘要、PDF 和表格。详见[0.8 发布说明与已知限制](docs/RELEASE_0.8.md)。

每条待审核数据旁边显示具体核对问题、原文证据和 PDF 原始页；可双击页图放大。审核自动保存，只有人工通过的数据才进入审核结果，缺温度等不满足条件的性能记录暂缓导出。详细步骤见[工作台简易操作说明](docs/WORKBENCH_QUICKSTART.md)。

新增可调整权重的文献阅读评分、PDF/图片坐标标定与取点，以及“提高十倍”等语义候选收集。图片读数关联论文、图号、样品和条件后进入待审核数值；语义候选单独导出，不转换成绝对性能。

评分用于安排阅读顺序，不能代替论文人工决定。本地 Cu-CHA 案例的正文通用规则只得到 3 条数值候选，说明跨论文提取覆盖率仍需评估。

## 摘要采集 EXE（第一轮论文筛选）

本地早期版本是 `NH3SCR_AbstractCollector_0.2`。支持浏览器采集 JSON、Zotero CSL JSON / RIS 和保存的 HTML，导出带来源与初筛原因的摘要清单。浏览器按钮源码位于 `browser_extension/`；安装步骤见[摘要采集说明](docs/ABSTRACT_COLLECTOR.md)。

2026-09-29 在本地取得 80 篇真实 Elsevier 题录的摘要。这是待人工确认的初筛清单，不随公开仓库发布，也不是性能训练集。

## Python 安装

需要 Python 3.10–3.13。在项目目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\scripts\workbench_gui.py
```

工作台按“统一检索或导入摘要 → 预取与核对论文 → 批量或逐篇获取正文 → 对照原文审核 → 导出”操作。接口设置只留在当前程序会话；Elsevier、Springer Nature、OpenAlex、Unpaywall 与开放来源按可用条件使用。Chrome 插件可采集当前页面可见的正文、图表和材料链接，也可用 Chrome 下载 PDF 与补充材料；采集包 JSON 导入工作台后，文件先保存在项目缓存，人工确认论文后才绑定为来源。所有文件仍需核对。源码仓库不包含本地 80 篇摘要；从 GitHub 克隆后，首页按钮会使用两条**明确标为合成数据**的演示摘要，也可以点击“添加自己的摘要文件”。

运行不依赖真实论文的自动测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

真实论文案例的附加测试在本地材料缺席时跳过。软件测试通过不等于跨论文数据质量达标。

## 旧版独立下载器（可选）

推荐从上面的工作台统一入口完成检索、核对和逐篇获取正文。`download_papers.cmd` 是保留给命令行批量任务的旧版独立入口，支持规则初筛、DeepSeek 和本机 Ollama；它的结果不会自动出现在工作台项目中，需要导入。

论文保存在 `outputs/paper_library/`，每次检索、筛选、人工核对表和下载状态保存在 `outputs/runs/`。第一次使用可勾选“只检索与筛选，暂不下载”。详细用法见[文献下载指南](docs/DOWNLOAD.md)。

## 数据提取

先用示例数据测试：

```powershell
.\run.cmd extract examples\raw.csv -o output\demo --mapping examples\mapping.json --paper-id SYNTHETIC_DEMO_NOT_REAL
```

核对 `output/demo/candidates.csv` 后，按[数据提取指南](docs/EXTRACTION.md)完成人工审核和导出。可输入 PDF、正文、表格及解析后的材料；示例数据不能用于科研训练。
