# 文献检索与下载

此入口检索 NH₃-SCR 论文题录，并尝试获取可以合法访问的 PDF 或出版社 XML。题录数量不等于实际下载数量；以 `download_manifest.csv` 中的状态为准。

## 安装

安装 Python 3.10–3.13，在项目根目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

不需要运行 PowerShell 脚本。若电脑用 `python` 而非 `py` 启动 Python，把第一行的 `py` 换成 `python`。

## 图形界面

双击项目根目录的 `download_papers.cmd`，填写检索词，例如 `NH3-SCR catalyst` 或具体 DOI，再设置每个来源的检索数与最多尝试下载数。Elsevier、Springer Nature、OpenAlex 密钥和 Unpaywall 邮箱都可选；请只在界面输入，不要写入仓库。

Elsevier 题录检索、摘要获取和全文获取是三个独立步骤。程序在筛选前尝试用 `META_ABS` 补取缺失摘要，用 `FULL` 请求全文 XML；401/403 单独记录为权限不足。检索成功不代表摘要或全文可用。机构提供 InstToken 时可设置 `ELSEVIER_INSTTOKEN`；全文还需相应的机构/API 访问权限。

缺失摘要会先按 DOI 从 OpenAlex、Semantic Scholar 和 Crossref 补充，最后尝试出版社公开摘要页面。这条路径不需要 Elsevier 密钥；接口限流或页面拒绝访问时保留状态。完整摘要缓存于 `outputs/paper_library/abstracts/`，再次检索可复用。来源与获取时间保存在 `abstract_source`、`abstract_url` 和 `abstract_fetched_at`。搜索页短简介单独标记，不作为完整摘要使用。

在界面点击“使用已有题录”，载入旧批次的 `records.json`，勾选“本次只检索与筛选”即可补摘要，不重复下载全文。命令行默认启用公开摘要补充；`--no-public-abstracts` 可关闭，`--abstract-page-limit` 控制最后尝试的出版社页面数。

### 摘要筛选和选择性下载

1. 初次使用选择“规则初筛”，无需 AI 密钥；检索数先设 10–20，最多下载先设 3。
2. 如果要让 AI 判断“实际研究的材料是否用于 NH₃-SCR”，选择“DeepSeek 摘要语义筛选”，在界面填入 DeepSeek key 和可用模型名称。该密钥与 Elsevier、Springer、OpenAlex 的文献接口密钥不同。模型名称可修改；默认示例是 `deepseek-flash`。
3. Ollama 是可选方式，只有本机已安装、启动 Ollama 并下载了相应模型时，才选择它并填写已安装模型名。无需同时使用 DeepSeek 和 Ollama。
4. 默认保留“待核对”论文的下载机会；它们可能只是缺摘要或缺少关键词。取消该选项时，待核对论文记为 `awaiting_review`，留在清单里。
5. 可以先勾选“本次只检索与筛选，暂不下载”。完成后核对 `screening_records.csv`；需要修改的行，在 `manual_review_template.csv` 的 `manual_decision` 填“相关”“不相关”或“待核对”，`reviewer` 填姓名，`reviewer_notes` 填理由。不修改的行留空。
6. 继续下载时，点击“使用已有题录”选择原 `records.json`，点击“载入人工核对表”选择填好的 CSV，取消“只检索与筛选”，再开始。它会沿用题录和人工决定，不重复搜索。

自动判断为相关不等于人工审核通过，也不等于论文中的数据可直接训练。综述、计算研究和摘要不明的记录会保留待核对。AI 请求失败、输出不完整或证据无法对应输入原文时，也转为待核对；报告中会统计错误。

`abstract_source` 标明摘要来源。例如 `nature_search_summary` 是出版社检索页的简要介绍，并非已确认的完整论文摘要；需要全文核对。PDF 没有明确摘要标题时，首页回退文字会留作待核对。

结果在 `outputs/runs/download_时间/`：

- `records.csv`：检索到并去重的论文题录。
- `screening_records.csv`：题目、摘要/简介、筛选决定、理由和来源；JSON 版还保留证据、AI 模型及用量。
- `manual_review_template.csv`：只需填写要修改的筛选决定及审核人，不必抄录论文。
- `screening_report.json`：相关/待核对/不相关数量、实际 AI 完成数和错误数。
- `download_manifest.csv`：每篇的实际下载状态与原因。
- `extraction_manifest.json`：全文文件名与 DOI 的对应关系，后续提取时自动使用。
- `files/`：本批次取得的全文。
- `summary.json`：题录、错误和下载数量汇总。

所有批次共用 `outputs/paper_library/`。重复 DOI 对应的已下载 PDF 显示为 `cached_pdf`，不会再次联网下载。本批次的 `files/` 使用硬链接指向同一文件。

`no_pdf` 只表示本次没有成功获取 PDF。例如出版社网页可读，但自动下载地址返回 403；它不表示论文不开放。付费全文需要相应的机构访问权限。请保留 DOI 与错误记录，便于人工补充。

## 命令行示例

```powershell
.\run.cmd harvest --query "NH3-SCR catalyst" --sources crossref nature --limit 20 --max-downloads 5 --output outputs\runs\trial
```

更多参数见 `run.cmd harvest --help`。

先筛选、不下载：

```powershell
.\run.cmd harvest --query "NH3-SCR catalyst" --sources nature crossref --limit 10 --no-download -o outputs\runs\screen_trial
```

使用已保存题录和人工决定，只获取相关论文：

```powershell
.\run.cmd harvest --records outputs\runs\screen_trial\records.json --review-decisions outputs\runs\screen_trial\manual_review_template.csv --no-download-review --max-downloads 3 -o outputs\runs\reviewed_download
```

DeepSeek 配置示例在 `examples/deepseek_screen_config.json`；密钥放在 `DEEPSEEK_API_KEY` 环境变量中，不写入配置。为 `harvest` 加上 `--screen-engine llm --screen-config examples/deepseek_screen_config.json` 即可启用。已有题录也可独立运行 `screen-metadata`，本地 PDF 使用 `screen` 命令；两者均支持 `--engine llm --config ...`。

筛选后 PDF 全文位于本批次 `files/`。对该目录使用 `extract` 时，程序自动读取旁边的 `extraction_manifest.json`，沿用 DOI，而不是把文件哈希当成论文身份。XML/JATS 的读取尚需另行转换。下载决定不会自动批准任何性能数据。
