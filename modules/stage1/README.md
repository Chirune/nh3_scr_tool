# 板块1：文献获取与筛选

当前版本：2026.10.15。检索 / DOI / 已有PDF / 出版社或Zotero接收；人工筛选后交给板块2。

Windows 双击本目录 `launch.cmd`，或在仓库根目录执行：

```powershell
.venv\Scripts\python.exe start_workbench.py --stage 1
```

[使用教程](../../docs/workbench/QUICKSTART.md) · [代码地图](../../docs/workbench/ARCHITECTURE.md) · [返回整合入口](../../README.md)

底层代码在 `catalyst_workbench/`，三个板块共用当前版本，避免复制后各自落后。运行数据统一写入 `runtime_data/`；独立入口也能按程序中的交接按钮进入下一板块。


本轮使用同一个完整源码包或 Windows 便携包。独立启动仍可双击本目录 launch.cmd，无需分别下载四个包。
