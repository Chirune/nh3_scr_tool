# 参与修改

项目目前有两个入口：[文献下载](docs/DOWNLOAD.md)和[数据提取](docs/EXTRACTION.md)。修改前请先在 Issue 中说明问题、论文 DOI、预期行为及实际状态；不要附上受版权限制的全文或密钥。

建议在单独分支修改，并提交 Pull Request。下载逻辑位于 `scrtool/harvest.py`，提取与筛选逻辑位于 `scrtool/extract.py`、`scrtool/ingest.py` 和 `scrtool/screening.py`。请保持 `record_id`、DOI、页码/表格行号、原始数值和核验状态可追溯；无法确定样品与实验条件的关联时，保留待核验，不补猜测值。

提交前运行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

请勿提交 `.venv/`、`outputs/`、`output/`、论文 PDF、模型权重、本机配置或 API 密钥。关于尚未申请专利的具体评分方法，先在团队内部讨论并确认披露范围，再决定是否加入共享仓库。
