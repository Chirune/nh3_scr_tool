# 板块3：标准化、评分与模型输入

当前版本：2026.10.15。读取待编码包，检查X/y、质量分与性能分，准备按论文分组的模型输入。

Windows 双击本目录 `launch.cmd`，或在仓库根目录执行：

```powershell
.venv\Scripts\python.exe start_workbench.py --stage 3
```

[使用教程](../../docs/workbench/QUICKSTART.md) · [代码地图](../../docs/workbench/ARCHITECTURE.md) · [返回整合入口](../../README.md)

底层代码在 `catalyst_workbench/`，三个板块共用当前版本，避免复制后各自落后。运行数据统一写入 `runtime_data/`；独立入口也能按程序中的交接按钮进入下一板块。


本轮使用同一个完整源码包或 Windows 便携包。独立启动仍可双击本目录 launch.cmd，无需分别下载四个包。
