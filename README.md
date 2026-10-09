# 催化文献数据工作台 · 三板块整合版

**当前共享版：2026.10.09。** 文献获取与筛选 → 单篇论文的文字、语义和图片核验 → 标准化、独立双评分与机器学习输入准备。

研究目标是把论文证据整理成可靠的数据，之后用催化剂配方和实验条件预测催化性能。当前已提供数据工作流；**尚未训练新预测模型，也没有新的预测准确率或已完成的DFT验证结果**。

## 从这里开始

- **普通组员**：[下载最新整合包](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-All-Latest.zip)，完整解压，双击 `start_workbench.cmd`。首次需Python 3.11/3.12及网络，用于安装本项目的独立依赖。
- **使用教程**：[从首次安装到三个板块完整流程](docs/workbench/QUICKSTART.md)。离线图文目录为 `docs/workbench/tutorial.html`，也可从整合窗口打开。
- **无需论文试用**：整合窗口点“无论文也能试：合成演示”。所有演示数据明确标为人工构造，不能用于科研训练。
- **已有组员采集项目**：点“导入组员采集项目”选择 `project.json`；保留论文和人工筛选，接收唯一有效正文PDF，原项目不被修改。

## 分板块与整合版

| 版本 | 入口 / 说明 | 独立下载 |
| --- | --- | --- |
| 第一板块：文献获取与筛选 | [stage1](modules/stage1/) | [板块1包](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-Stage1-Latest.zip) |
| 第二板块：文字、语义、图像核验 | [stage2](modules/stage2/) | [板块2包](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-Stage2-Latest.zip) |
| 第三板块：编码、评分、模型准备 | [stage3](modules/stage3/) | [板块3包](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-Stage3-Latest.zip) |
| 三板块整合版 | 根目录 `start_workbench.cmd` | [整合包](https://github.com/Chirune/nh3_scr_tool/releases/latest/download/CatalystWorkbench-All-Latest.zip) |

四个包使用同一版本底层代码。分板块包提供对应默认入口，同时携带交接和回看来源所需的共享组件。新入口没有内置个人论文或历史运行结果。

## 本版重点

1. **有用数值**：区分性能、实验条件和材料属性；保留区间、上下界与近似，显示样品和条件的核对事项。
2. **科研语义**：比较、倍数、比值、计算约束、定性、否定、预期及科研关系；原句、适用条件和来源保留；审核后决定用途。
3. **中文阅读备注**：原文下显示参考译文、术语和提示。公开模型首次单独安装，之后本机翻译；译文不直接变成标签。
4. **图像核验**：图号/图注候选、裁剪、多子图读数、坐标与系列核对，输出原图叠加与CSV，并回传所属论文。
5. **第三板块**：单篇证据汇总后标准化；质量分与性能分分开；输入可得性、按论文分组与训练折内预处理。

## 源码运行和验证

```powershell
py scripts/setup_workbench.py --team-tools
.venv\Scripts\python.exe start_workbench.py
.venv\Scripts\python.exe scripts/test_shared_workbench.py
```

当前桌面功能以Windows 10/11为目标环境，自动图像OCR使用Windows组件。模型训练和DFT不包含在当前操作入口中。中文翻译安装：

```powershell
.venv\Scripts\python.exe scripts/setup_translation.py
```

[本版验证范围](docs/workbench/VALIDATION.md) · [代码地图与交接](docs/workbench/ARCHITECTURE.md) · [团队协作](CONTRIBUTING.md)

## 组员已有工具

`scrtool/`、`scripts/workbench_gui.py` 和 `browser_extension/` 保留原有采集功能，整合窗口可以进入；[浏览器采集说明](docs/BROWSER_CAPTURE.md)。原有规则阅读排序与新板块的数据质量/性能评价是不同用途。

运行结果写入 `runtime_data/`，不上传GitHub。公开仓库只分享源码、使用说明、合成示例生成器和测试；个人PPT、专利材料、论文全文、账号凭据、科研档案和模型权重保留在本机。参见[分享范围](docs/GITHUB_SHARE.md)。
