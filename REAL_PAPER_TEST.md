# 真实论文测试记录

测试日期：2026-09-12。原始输入是“论文”文件夹中的 *Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts*，DOI：10.1038/s41467-026-72879-7。

## 查看结果

- [交互式核验报告](output/real_paper/report.html)：可筛选记录，查看图中数据点的绿色叠加框。
- [全部 150 条观测](output/real_paper/all_observations.csv)：包含待核验数据。
- [性能长表](output/real_paper/training/ml_long.csv)：105 行，101 个图中读数 + 4 个正文临界 ANR 值。
- [机器学习特征宽表](output/real_paper/training/ml_features.csv)：105 行，保留条件、已核验组成/制备特征和来源记录 ID。
- [14 条待核验记录](output/real_paper/needs_review.csv)。
- [来源冲突](output/real_paper/conflicts.json)。
- [九个视觉锚点的检查结果](output/real_paper/visual_anchor_checks.json)。

原始论文文件没有修改。主文 14 页；另从出版商公开页面取得并解析了 39 页[补充材料](https://media.springernature.com/original/springer-static/esm/art%3A10.1038%2Fs41467-026-72879-7/MediaObjects/41467_2026_72879_MOESM1_ESM.pdf)。下载位置为 `output/real_paper/downloads/supplementary.pdf`。作者在正文 Data availability 中说明原始数据需向作者索取；本次没有取得作者原始实验数据，也没有联系作者。

## 实测发现与修复

1. 原版规则模式在此论文上得到 0 条候选。不能将“脚本运行成功”当作“数据获取成功”。新增显式样品—负载量配对和 PDF 表格读取。
2. 补充表的 Si、Al、Si/Al 跨三行合并。新增基于单元格几何覆盖范围的读取，普通空白格不会随意向下填充。
3. Fig.1a 中的 46 个离散标记由 PDF 矢量对象提取，重复的描边/填充去重，连接线和图例排除。
4. Fig.1b 的 55 个标记按六条曲线提取。Cu-0.5 使用左侧 5–30% 的轴，Cu-1.4 使用右侧 0–100% 的轴。错误地共用 Y 轴会明显污染结果。
5. Cu-0.5 铜含量：主文 0.48 wt%，补充表 0.47 wt%；Cu-1.4：主文叙述 1.38 wt%，图注/方法/补充表 1.39 wt%。冲突记录仍保留，特征宽表对应铜含量留空。
6. CMI-FA 的 `2.42 (1.39)` 表示总 Cu 与 EPR 测得的孤立 Cu，不能当作误差或一个普通数值。该类复合记录保留待核验，没有自动拆分。
7. 主文标准 SCR 的空速为 150,000 h⁻¹；EPR 方法另有约 400,000 h⁻¹。没有把 EPR 条件套用到 Fig.1 的性能值。
8. Windows 启动器优先使用本项目 `.venv`，排除 Python 商店占位程序。MinerU 源码漏声明的 `six` 依赖已补齐。

## 核验含义与覆盖范围

150 条观测中，136 条标为 `approved`，其 `review_level=agent_checked`，`reviewer` 明确写明是 Codex 代理的视觉/证据检查，**不代表领域专家人工审核**。另外 14 条保持 pending。

这是单篇论文的功能和数据完整性测试。矢量图配置是针对原文件视觉标定的，正文补充采用明确标记的证据标注，不能据此宣称跨论文的全自动提取精确率/召回率。正文原值、证据、页码、图号和 PDF 哈希均保留。曲线点的 `estimated=true`，代表从排版后的图中读取，不是原始实验数值；小数位不代表测量精度。

已完成：主文 Fig.1a/1b、补充表 1、选定制备步骤与正文临界 ANR。其他动力学/DFT/EPR/TPD 图和补充性能曲线已在页面检查材料中保留，尚未逐幅数字化。不将计算能垒混作实验活化能，不从曲线图注虚构缺失的 BET 等参数。

只有一篇论文，不能训练并验证具有泛化能力的催化剂预测模型。`split_group` 使用 DOI；实际扩充多篇文献后，应按论文分组划分训练与测试。临界 ANR 和 NOx 转化率是不同目标，建模前按 `target_property` 筛选。

## 安装与复现

本机安装位置：`D:\数据检索\nh3_scr_tool\.venv`。使用 Python 3.12，已安装基础 PDF/表格依赖和 MinerU 3.4.5 pipeline。MinerU 已在 CPU 模式完成主文全部 14 页，生成 Markdown、内容 JSON、版面检查 PDF 和 20 张图像；结果保存在 `output/real_paper/mineru`。首次运行下载模型较慢，后续使用本地缓存。

没有安装 Marker、OpenChemIE 和 ChemDataExtractor2，它们不是本次数据路径的必要依赖，且 Marker 的依赖版本与当前 MinerU 不兼容，不能合装。

```powershell
cd D:\数据检索\nh3_scr_tool
# 当前环境已安装；迁移/重建时才运行
.\install.ps1 -WithMinerU

# 使用已下载的主文和补充材料，重新生成本案例全部数据并运行测试
.\reproduce_real_case.ps1

# 单独提取图，配置中锁定原 PDF 的 SHA-256
.\run.ps1 vector '..\论文\Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts.pdf' --profile profiles\deka_2026_fig1a.json -o output\check_fig1a

# MinerU 独立转换；首次下载权重。关闭本测试不需要的公式/表格模型
.\run.ps1 convert '..\论文\Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts.pdf' -o output\mineru_check --no-formula --no-tables
```

安装日志：`output/mineru_install.log`；实际转换日志：`output/mineru_run_retry.log`。安装环境版本锁定文件为 `requirements.lock.txt`，其中 MinerU 的本地源码 URI 依赖当前目录位置；迁移机器优先使用 `install.ps1`。
