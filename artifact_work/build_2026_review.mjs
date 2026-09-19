import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "D:/数据检索/nh3_scr_tool/outputs/01a093dc-0106-7ce3-8d6b-da5f34ce09db";
const outputPath = `${outputDir}/2026_CuCHA_5项审核.xlsx`;
const mainPreviewPath = `${outputDir}/2026_CuCHA_5项审核_preview.png`;
const evidencePreviewPath = `${outputDir}/2026_CuCHA_证据_preview.png`;

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("需要你判断");
sheet.showGridLines = false;
sheet.getRange("A1:H1").values = [["编号", "你要判断什么", "原文中的值", "程序建议", "你的选择", "你的修改或说明", "影响", "关联记录"]];
sheet.getRange("A2:H6").values = [
  ["D001", "Cu-0.5 的铜含量采用哪个数值？", "正文/图注：0.48 wt%；补充表1：0.47 wt%", "训练特征采用补充表测量值 0.47 wt%；0.48 作为正文报告值保留在来源记录中。", "", "", "组成特征", "1, 4, 13"],
  ["D002", "Cu-1.4 的铜含量采用哪个数值？", "正文：1.38 wt%；图注和补充表1：1.39 wt%", "训练特征采用补充表测量值 1.39 wt%；1.38 作为正文报告值保留在来源记录中。", "", "", "组成特征", "2, 5, 14"],
  ["D003", "补充图1的 200 °C 属于哪些样品？", "图注说明 fresh/aged，即 CMI-DG 与 CMI-FA", "把 200 °C 作为同一图中 CMI-DG 和 CMI-FA 两条数据系列的共同反应条件。", "", "", "样品—条件关联", "3"],
  ["D004", "表格中的“--”如何处理？", "CMI-DG、CMI-FA 的 Si 和 Al 含量为 --", "解释为作者未报告；保留缺失状态，不建立数值记录，也不填 0。", "", "", "缺失值", "6–9"],
  ["D005", "CMI-FA 括号内数值表示什么？", "Cu：2.42 (1.39) wt%；Cu/Al：0.30 (0.18)；Cu/笼：0.29 (0.17)", "按脚注拆分：括号外为总 Cu；括号内为 EPR 测得的孤立 Cu。分别保存 total 与 isolated 两组特征。", "", "", "复合表格值", "10–12"],
];
sheet.freezePanes.freezeRows(1);
sheet.getRange("A1:H6").format.font = { name: "Arial", size: 11, color: "#1F2937" };
sheet.getRange("A1:H1").format = { fill: "#1F4E78", font: { name: "Arial", size: 11, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
sheet.getRange("A2:H6").format.verticalAlignment = "top";
sheet.getRange("B2:H6").format.wrapText = true;
sheet.getRange("E2:F6").format.fill = "#FFF2CC";
sheet.getRange("E2:E6").dataValidation = { rule: { type: "list", values: ["接受建议", "需要修改", "不确定"] } };
sheet.getRange("A1:H6").format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
const widths = [9, 28, 39, 52, 15, 34, 18, 15];
widths.forEach((width, index) => { sheet.getRangeByIndexes(0, index, 6, 1).format.columnWidth = width; });
sheet.getRange("1:1").format.rowHeight = 34;
sheet.getRange("2:6").format.rowHeight = 76;

const evidence = workbook.worksheets.add("原文证据");
evidence.showGridLines = false;
evidence.getRange("A1:D1").values = [["编号", "位置", "原文/表格内容", "解释"]];
evidence.getRange("A2:D6").values = [
  ["D001", "正文第2–3页；补充表1第1行", "Cu-0.5: 0.48 wt%（正文/图注）；0.47 wt%（Supplementary Table 1）", "同一样品在出版物不同位置有 0.01 wt% 的舍入差异。"],
  ["D002", "正文第2–3页；补充表1第2行", "Cu-1.4: 1.38 wt%（正文）；1.39 wt%（图注/Supplementary Table 1）", "同一样品在出版物不同位置有 0.01 wt% 的舍入差异。"],
  ["D003", "补充材料第8页；Supplementary Figure 1", "Standard SCR NOx conversion at ... 200 °C ... on degreened (DG) and field aged (FA) samples.", "200 °C 是图中两个状态样品共同的测试条件。"],
  ["D004", "补充材料第7页；Supplementary Table 1", "CMI-DG 与 CMI-FA 的 Si content、Al content 单元格为 --。", "原文没有给出这些含量；-- 不是数值。"],
  ["D005", "补充材料第7页；Supplementary Table 1 脚注 a", "Values within parenthesis show isolated Cu content measured by EPR ... and Cu/Al ratio and Cu-ion per cage based on such isolated Cu content.", "括号外是总 Cu 口径；括号内是孤立 Cu 口径。"],
];
evidence.getRange("A1:D6").format.font = { name: "Arial", size: 11, color: "#1F2937" };
evidence.getRange("A1:D1").format = { fill: "#1F4E78", font: { name: "Arial", size: 11, bold: true, color: "#FFFFFF" } };
evidence.getRange("A2:D6").format.verticalAlignment = "top";
evidence.getRange("B2:D6").format.wrapText = true;
evidence.getRange("A1:D6").format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
[9, 28, 78, 48].forEach((width, index) => { evidence.getRangeByIndexes(0, index, 6, 1).format.columnWidth = width; });
evidence.getRange("1:1").format.rowHeight = 34;
evidence.getRange("2:6").format.rowHeight = 76;

await fs.mkdir(outputDir, { recursive: true });
const mainPreview = await workbook.render({ sheetName: "需要你判断", range: "A1:H6", scale: 1, format: "png" });
await fs.writeFile(mainPreviewPath, new Uint8Array(await mainPreview.arrayBuffer()));
const evidencePreview = await workbook.render({ sheetName: "原文证据", range: "A1:D6", scale: 1, format: "png" });
await fs.writeFile(evidencePreviewPath, new Uint8Array(await evidencePreview.arrayBuffer()));
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
