import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const source = "D:/数据检索/nh3_scr_tool/outputs/01a093dc-0106-7ce3-8d6b-da5f34ce09db/gold_2020_review.xlsx";
const preview = "D:/数据检索/nh3_scr_tool/outputs/01a093dc-0106-7ce3-8d6b-da5f34ce09db/gold_2020_review_guide_preview.png";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const image = await workbook.render({ sheetName: "说明", range: "A1:B15", scale: 1.5, format: "png" });
await fs.writeFile(preview, new Uint8Array(await image.arrayBuffer()));
const table = await workbook.inspect({ kind: "table", sheetId: "说明", range: "A1:B15", include: "values,formulas", tableMaxRows: 15, tableMaxCols: 2, maxChars: 5000 });
console.log(table.ndjson);
