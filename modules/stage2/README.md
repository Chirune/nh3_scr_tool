# 板块2：文字、语义与图像证据

当前版本：2026.10.15。以一篇论文为单位提取三路证据，逐项审核，交给第三板块。

Windows 双击本目录 `launch.cmd`，或在仓库根目录执行：

```powershell
.venv\Scripts\python.exe start_workbench.py --stage 2
```

[使用教程](../../docs/workbench/QUICKSTART.md) · [代码地图](../../docs/workbench/ARCHITECTURE.md) · [返回整合入口](../../README.md)

底层代码在 `catalyst_workbench/`，三个板块共用当前版本，避免复制后各自落后。运行数据统一写入 `runtime_data/`；独立入口也能按程序中的交接按钮进入下一板块。


本轮使用同一个完整源码包或 Windows 便携包。独立启动仍可双击本目录 launch.cmd，无需分别下载四个包。
