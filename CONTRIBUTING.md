# 参与修改

当前入口是根目录 `start_workbench.cmd`，也可分别从 `modules/stage1/`、`modules/stage2/`、`modules/stage3/` 启动。先阅读[当前教程](docs/workbench/QUICKSTART.md)与[代码地图](docs/workbench/ARCHITECTURE.md)。报告问题时说明论文 DOI、预期行为及实际状态；不要附上受版权限制的全文或密钥。

建议在单独分支修改，并提交 Pull Request。最新三板块源码在 `catalyst_workbench/`；组员原有采集功能在 `scrtool/`、`scripts/` 和 `browser_extension/`。请保持记录ID、DOI、页码/表格行号、原始数值和核验状态可追溯；无法确定样品与实验条件的关联时，保留待核验，不补猜测值。跨板块修改需要同时检查交接与来源核验。

提交前运行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
.\.venv\Scripts\python.exe scripts/test_shared_workbench.py
```

请勿提交 `.venv/`、`runtime_data/`、`local_models/`、`outputs/`、`output/`、论文 PDF、个人PPT、私人专利资料、本机配置或 API 密钥。后续增加尚未公开的专利构思时，先确认团队拟披露范围。当前共享范围见[分享说明](docs/GITHUB_SHARE.md)。
