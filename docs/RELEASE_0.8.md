# NH3-SCR 工作台 0.8 公开交付包

打包日期：2026-10-02。解压 Windows 包后双击 `NH3SCR_Workbench.exe`，无需安装 Python。Chrome 插件位于同目录 `browser_extension/`，在 Chrome 扩展程序页打开开发者模式，选择“加载已解压的扩展程序”并选中该目录。已安装旧版插件时重新选择此目录或更新原目录后刷新扩展。

公开包只内置两条标明为合成数据的演示摘要，不含本地 80 篇真实摘要、论文全文、用户项目、API 密钥或测试输出。第一次使用可以用演示摘要熟悉流程，再导入自己的论文材料。

操作顺序：统一检索或导入摘要 → 初筛与人工确认 → 批量获取正文或从浏览器采集 → 对照原文核对候选数据 → 导出审核结果。浏览器插件采集当前页可见的正文、表格、图片与材料链接，可发起 PDF 和补充材料下载；登录权限由浏览器管理。

本次发布前 116 项自动测试通过；公开 EXE 的两条演示摘要导入、评分、模拟正文获取归档、单位转换、原文显示、图中读数接口、审核保存恢复与导出门槛验收通过。这是软件流程验收，实际出版社权限和跨论文抽取精度仍按下列限制评估。

## 当前限制

- 出版社接口和批量调度已实现，但下载是否成功取决于论文来源与实际访问权限。
- 2026-10-01 的实际权限测试中，同一 key 的 Scopus 检索返回 200；ScienceDirect 检索返回 401 授权错误；Article Retrieval 的 META_ABS 和 PDF FULL 请求返回 403，原因为 `Requestor configuration settings insufficient for access to this resource.`。当前 ScienceDirect API 获取链路尚未通过实际权限验证，不能宣称自动下载所有 Elsevier 论文。需要 Elsevier 核查接口配置、机构 IP 或 InstToken；浏览器可读不自动授予 Python 接口权限。
- 数据提取结果仍是待核对候选，跨论文覆盖率还在测试。导出成功不代表已形成能直接训练模型的数据集；机器学习、SISSO、实验和 DFT 验证尚未完成。

## 发布文件

- `NH3SCR_Workbench_0.8_Windows.zip`：EXE、Chrome 插件与操作说明，适合直接使用。
- `NH3SCR_0.8_Source.zip`：源码、依赖清单、少量合成示例、测试代码和项目说明，适合开发与复现。

将以上文件作为 GitHub Release 附件；EXE、论文与运行结果不放进源码提交。测试代码属于源码，生成的测试结果不随包提供。

## 从源码构建公开版

在 Windows、Python 3.10–3.13 环境下，按 README 安装依赖，然后执行：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\scripts\build_workbench_exe.ps1
```

默认输出 `artifact_work/NH3SCR_Workbench_0.8_Public/`，只内置合成示例。`-IncludeLocalAbstracts` 仅用于本机内部使用，生成的包不应作为本公开 Release 附件。
