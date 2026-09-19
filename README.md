# NH₃-SCR 文献下载与数据提取

用于检索 NH₃-SCR 催化剂论文、获取可访问的全文，并从论文和原始表格中提取催化剂、反应条件及性能数据。提取结果需人工核验后才能用于机器学习。

## 安装

需要 Python 3.10–3.13。在项目目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 文献下载

Windows 双击 `download_papers.cmd`，填写检索词并启动。论文保存在 `outputs/paper_library/`，每次检索的题录和下载状态保存在 `outputs/runs/`。详细用法见[文献下载指南](docs/DOWNLOAD.md)。

## 数据提取

先用示例数据测试：

```powershell
.\run.cmd extract examples\raw.csv -o output\demo --mapping examples\mapping.json --paper-id SYNTHETIC_DEMO_NOT_REAL
```

核对 `output/demo/candidates.csv` 后，按[数据提取指南](docs/EXTRACTION.md)完成人工审核和导出。可输入 PDF、正文、表格及解析后的材料；示例数据不能用于科研训练。
