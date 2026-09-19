import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const sourcePath = "D:/数据检索/nh3_scr_tool/evaluation/gold_2020_draft.csv";
const outputDir = "D:/数据检索/nh3_scr_tool/outputs/01a093dc-0106-7ce3-8d6b-da5f34ce09db";
const outputPath = `${outputDir}/gold_2020_review.xlsx`;
const previewPath = `${outputDir}/gold_2020_review_preview.png`;

const csvText = await fs.readFile(sourcePath, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "审核表" });
const sheet = workbook.worksheets.getItem("审核表");

// Repair the cell that was corrupted when the UTF-8 CSV was opened by Excel.
sheet.getRange("B10").values = [["main PDF page 3; Fig.2b; Supplementary Fig.7"]];

sheet.getRange("K1:L1").values = [["字段说明（中文）", "符号与审核提示"]];
const explanations = {
  G001: ["活性中心类型：相邻的单原子 Mo 与表面 Fe 构成双核位点。", "定性字段；判断作者是否给出了足够证据。"],
  G002: ["结构模型中的 Mo-Fe 活性位距离。", "Å 是埃，1 Å = 0.1 nm。"],
  G003: ["EXAFS 拟合得到的 Mo-O 配位数。", "CN 是配位数。"],
  G004: ["EXAFS 拟合得到的 Mo-O 键长。", "1.88(7) 表示中心值 1.88 Å，括号为末位不确定度。"],
  G005: ["EXAFS 拟合的第一组 Mo-Fe 配位数。", "需与壳层和键长成组保留。"],
  G006: ["EXAFS 拟合的第一组 Mo-Fe 距离。", "Å 是埃，1 Å = 0.1 nm。"],
  G007: ["EXAFS 拟合的第二组 Mo-Fe 配位数。", "需与壳层和键长成组保留。"],
  G008: ["EXAFS 拟合的第二组 Mo-Fe 距离。", "Å 是埃，1 Å = 0.1 nm。"],
  G009: ["Mo 的氧化态为 +5。作者根据 XANES 峰面积判断，并用 Mo 3d XPS 佐证。", "数值 5 与单位 + 合起来表示 Mo⁵⁺；证据等级是“多种表征支持”。"],
  G010: ["酸位类型：MoO6H 提供 Brønsted 酸位，作者认为其在 SCR 温度下可转为 Lewis 酸位。", "这是作者的机理解释，不是一个连续数值。"],
  G011: ["相对于 α-Fe2O3 的 Mo 质量负载量。", "wt% 表示质量百分数。"],
  G012: ["表观活化能及其报告误差。", "86 ± 4 表示中心值 86、误差 4 kJ/mol。"],
  G013: ["单位活性中心每秒转化的 NO 分子数。", "~ 表示近似值；TOF 约为 1.7×10⁻³ s⁻¹。"],
  G014: ["标准活性评价在大气压力下进行。", "保留为报告条件；不自动换算为精确压力。"],
  G015: ["入口 NO 浓度。", "属于标准活性评价条件。"],
  G016: ["入口 NH3 浓度。", "属于标准活性评价条件。"],
  G017: ["入口 O2 体积分数。", "vol% 表示体积百分数。"],
  G018: ["标准活性评价的气体体积空速。", "单位 h⁻¹。"],
  G019: ["耐水、耐硫实验温度。", "与标准升温活性曲线不是同一实验。"],
  G020: ["耐硫实验使用的入口 SO2 浓度。", "只在加入 SO2 的阶段适用。"],
  G021: ["耐水实验使用的入口 H2O 体积分数。", "只在加入 H2O 的阶段适用。"],
  G022: ["补充图 11：不同 Mo 负载量及对照样的 NO 转化率-温度曲线。", "PDF 为矢量图，将使用自动标记读取工具。"],
  G023: ["补充图 12：转化率、N2O 和 N2 选择性四个子图。", "PDF 为矢量图；每个子图和坐标轴分别标定。"],
  G024: ["补充图 13：加入 H2O/SO2 前后，NO 转化率随时间变化。", "PDF 为矢量图；断轴和加入/切断时间需要单独处理。"],
};

const rows = sheet.getRange("A2:J25").values;
const notes = rows.map(row => explanations[String(row[0])] || ["", ""]);
sheet.getRange("K2:L25").values = notes;

sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);
sheet.freezePanes.freezeColumns(1);
sheet.getRange("A1:L25").format.font = { name: "Arial", size: 10, color: "#1F2937" };
sheet.getRange("A1:L1").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
};
sheet.getRange("A2:L25").format.verticalAlignment = "top";
sheet.getRange("B2:B25").format.wrapText = true;
sheet.getRange("G2:L25").format.wrapText = true;
sheet.getRange("I2:J25").format.fill = "#FFF2CC";
sheet.getRange("I2:I25").dataValidation = { rule: { type: "list", values: ["approve", "reject", "uncertain"] } };
sheet.getRange("A1:L25").format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
const widths = [9, 31, 23, 25, 14, 11, 34, 24, 15, 28, 49, 45];
widths.forEach((width, index) => { sheet.getRangeByIndexes(0, index, 25, 1).format.columnWidth = width; });
sheet.getRange("1:1").format.rowHeight = 34;
sheet.getRange("2:25").format.rowHeight = 48;

const guide = workbook.worksheets.add("说明");
guide.showGridLines = false;
guide.getRange("A1:B1").values = [["2020 论文数据审核说明", ""]];
guide.getRange("A3:B9").values = [
  ["审核值", "含义"],
  ["approve", "字段、样品、数值、单位、条件和证据等级正确。"],
  ["reject", "该记录不应纳入；请在审核意见中写明原因。"],
  ["uncertain", "当前证据不足，保留待定，不猜测。"],
  ["G009", "Mo 氧化态 +5。这里不是性能数值，而是活性中心的结构/电子状态特征。"],
  ["G010", "Brønsted 酸位在 SCR 温度下可能转为 Lewis 酸位，属于作者的机理解释。"],
  ["曲线", "补充图 11-13 是矢量图。程序可读取图形标记；图 13 的断轴需专门配置。"],
];
guide.getRange("A11:B15").values = [
  ["符号", "说明"],
  ["Å", "埃；1 Å = 0.1 nm。"],
  ["±", "报告值的不确定度或误差范围，例如 86 ± 4。"],
  ["Brønsted", "布朗斯特酸位；工作簿内部使用 Unicode，不会按 CSV 本地编码损坏。"],
  ["~", "近似值，不能当作未经限定的精确值。"],
];
guide.getRange("A1:B15").format.font = { name: "Arial", size: 11, color: "#1F2937" };
guide.getRange("A1:B1").format.font = { name: "Arial", size: 15, bold: true, color: "#1F4E78" };
guide.getRange("A3:B3").format = { fill: "#1F4E78", font: { name: "Arial", size: 11, bold: true, color: "#FFFFFF" } };
guide.getRange("A11:B11").format = { fill: "#1F4E78", font: { name: "Arial", size: 11, bold: true, color: "#FFFFFF" } };
guide.getRange("A3:B15").format.wrapText = true;
guide.getRange("A1:A15").format.columnWidth = 18;
guide.getRange("B1:B15").format.columnWidth = 78;
guide.getRange("3:15").format.rowHeight = 34;

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({ sheetName: "审核表", range: "A1:L25", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const guidePreviewPath = path.join(outputDir, "gold_2020_review_guide_preview.png");
const guidePreview = await workbook.render({ sheetName: "说明", range: "A1:B15", scale: 1.5, format: "png" });
await fs.writeFile(guidePreviewPath, new Uint8Array(await guidePreview.arrayBuffer()));
const result = await workbook.inspect({ kind: "table", sheetId: "审核表", range: "A1:L12", include: "values,formulas", tableMaxRows: 12, tableMaxCols: 12, maxChars: 8000 });
console.log(result.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
console.log(errors.ndjson);
const out = await SpreadsheetFile.exportXlsx(workbook);
await out.save(outputPath);
console.log(outputPath);
