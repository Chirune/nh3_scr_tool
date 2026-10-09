# 板块3：标准化、评分与模型输入

当前版本：2026.10.09。读取待编码包，检查X/y、质量分与性能分，准备按论文分组的模型输入。

Windows 双击本目录 `launch.cmd`，或在仓库根目录执行：

```powershell
.venv\Scripts\python.exe start_workbench.py --stage 3
```

[使用教程](../../docs/workbench/QUICKSTART.md) · [代码地图](../../docs/workbench/ARCHITECTURE.md) · [返回整合入口](../../README.md)

底层代码在 `catalyst_workbench/`，三个板块共用当前版本，避免复制后各自落后。运行数据统一写入 `runtime_data/`；独立入口也能按程序中的交接按钮进入下一板块。

独立包：[下载板块3当前版](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-Stage3-Latest.zip)。独立包也带必需的共享组件，默认从本板块进入；不需要同时安装四份。
