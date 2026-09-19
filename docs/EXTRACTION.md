# 论文数据提取

此入口把 NH₃-SCR 论文的正文、表格和原始 CSV/Excel 转成**待核验的候选数据**。它不会自动把不确定数值当成可靠训练样本。

## 安装

安装 Python 3.10–3.13，在项目根目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 最小可运行示例

Windows 命令提示符或 PowerShell 均可使用 `run.cmd`：

```powershell
.\run.cmd extract examples\raw.csv -o output\demo --mapping examples\mapping.json --paper-id SYNTHETIC_DEMO_NOT_REAL
```

`examples/raw.csv` 是合成测试数据，不是研究数据。检查 `output/demo/candidates.csv`、`sources.json`、`decisions_template.csv` 和 `review_queue.json`。实际论文可使用：

```powershell
.\run.cmd screen "D:\论文文件夹" -o output\screening
.\run.cmd extract "D:\论文文件夹\paper.pdf" -o output\paper --paper-id "10.xxxx/your-doi"
```

人工比对原文后，在 `decisions_template.csv` 填写 `decision=approve` 或 `reject`、`reviewer` 和核对说明，然后运行：

```powershell
.\run.cmd review output\paper\candidates.json output\paper\decisions_template.csv -o output\paper\reviewed.json
.\run.cmd export output\paper\reviewed.json -o output\training
```

机器学习输入在 `output/training/ml_long.csv`。每行保留论文 ID、催化剂、反应条件、性能指标、单位和证据出处。曲线估读值与原始实验数字要区分；不同论文及不同处理状态的样品不能随意合并。完整字段、复杂 PDF 转换和图线取点见项目 [README](../README.md)。

论文 PDF、原始全文、提取输出及 API 密钥保留在本机或团队批准的数据存储中，不提交 GitHub。
