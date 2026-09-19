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

结果在 `outputs/runs/download_时间/`：

- `records.csv`：检索到并去重的论文题录。
- `download_manifest.csv`：每篇的实际下载状态与原因。
- `files/`：本批次取得的全文。
- `summary.json`：题录、错误和下载数量汇总。

所有批次共用 `outputs/paper_library/`。重复 DOI 对应的已下载 PDF 显示为 `cached_pdf`，不会再次联网下载。本批次的 `files/` 使用硬链接指向同一文件。

`no_pdf` 只表示本次没有成功获取 PDF。例如出版社网页可读，但自动下载地址返回 403；它不表示论文不开放。付费全文需要相应的机构访问权限。请保留 DOI 与错误记录，便于人工补充。

## 命令行示例

```powershell
.\run.cmd harvest --query "NH3-SCR catalyst" --sources crossref nature --limit 20 --max-downloads 5 --output outputs\runs\trial
```

更多参数见 `run.cmd harvest --help`。
