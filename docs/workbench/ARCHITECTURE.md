# 三板块代码地图与团队分工

当前整合：2026.10.09。一个底层代码目录，三个独立入口，再加根目录整合入口。修改共享源码后，所有入口使用同一实现。

```text
start_workbench.py / .cmd       整合入口
modules/stage1/                 第一板块入口与说明
modules/stage2/                 第二板块入口与说明
modules/stage3/                 第三板块入口与说明
catalyst_workbench/
  gateway/                     获取、筛选、PDF获取、Zotero与出版社设置
  paper_app.py                 以论文为单位组织三路证据
  paper_numeric*.py            数值识别、用途与原文核对
  paper_semantics.py           语义候选
  paper_relations.py           嵌套关系、条件与来源
  paper_semantic_facets.py     科研关系与条件角色
  paper_semantic_audit.py      语义审核一致性
  paper_reading.py             中文阅读备注与独立缓存
  paper_translation_worker.py 本地翻译进程
  paper_glossary.py            专业术语
  app.py / locate.py           PDF找图、裁剪与候选审核
  digitizer/                   坐标、系列、曲线/散点/柱形读数与核验
  paper_workspace.py           单篇档案与交接
  paper_image_bridge.py        图像读数回传与来源复验
  coding_app.py                第三板块界面
  paper_encoding.py           标准化、任务准入、X/y
  paper_scoring.py            质量与性能分别打分
  ml_preparation*.py          按论文分组、模型输入准备
  team_bridge.py              接收组员原采集工作台的论文项目
  workbench_paths.py          统一数据/模型目录
scripts/setup_workbench.py     独立环境安装
scripts/setup_translation.py  固定来源与校验值的翻译模型安装
scripts/test_shared_workbench.py 全套离线功能检查
docs/workbench/                当前教程、版本与来源
scrtool/ + browser_extension/  组员原有采集与浏览器功能
```

## 交接约定

- 板块1 `run.json` → 板块2。保留人工决定、PDF路径与SHA256；缺PDF时显示待办。
- 板块2以 `paper.json` 为本篇档案。三路证据都有独立来源，审核变化后重新导出。
- 板块2 `待编码包.json` → 板块3。由程序导出，不能靠重命名其他JSON冒充。
- 编码结果保留观察ID、DOI、指标、单位、缺失、来源及论文分组。新语义、图像估读和人工审核是不同层次。
- `project.json` 为原组员采集工具格式。当前导入桥只接论文、人工筛选和唯一正文PDF，不自动迁移旧数值审核。
- 审核和科学数据是本机文件，不自动推送到GitHub；共享科研数据需要另行确定目录、权限和披露范围。

## 验证

```powershell
.venv\Scripts\python.exe scripts/test_shared_workbench.py
```

核心、获取、读图与原仓库测试分别在独立进程运行，避免同名模块干扰。合成测试验证程序行为，不是论文抽取准确率或预测准确率。需要真实论文的附加测试在无本地材料时跳过。

`source_manifest.json` 记录纳入的最新源文件初始校验值；整合后的路径适配和新增入口由Git差异记录。`translation_model.json` 固定模型来源、许可说明和内容SHA256；模型本身不入Git。

## 协作方式

为一项修改创建分支，限定负责的模块，提交前运行相关测试。说明触发问题、修改行为与验证结果。跨板块修改要核对上述交接文件；不要直接修改或提交个人科研输出。依赖变更需同步安装清单和验证记录。
