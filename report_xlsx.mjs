import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("Uso: node report_xlsx.mjs dados.json saida.xlsx");
const report = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const sheet = workbook.worksheets.add(report.title.slice(0, 31));
sheet.showGridLines = false;
sheet.tabColor = "#167A62";

const columnCount = report.columns.length;
const lastColumn = columnName(columnCount);
sheet.getRange(`A1:${lastColumn}1`).merge();
sheet.getRange("A1").values = [[report.title]];
sheet.getRange(`A1:${lastColumn}1`).format = {
  fill: "#DFF1E9",
  font: { name: "Arial", size: 15, bold: true, color: "#17211F" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  rowHeight: 28,
  borders: { preset: "outside", style: "medium", color: "#17211F" },
};
sheet.getRange(`A3:${lastColumn}4`).format.font = { name: "Arial", size: 9 };
sheet.getRange("A3:B4").values = [
  ["OBRA", report.obra.nome],
  ["FORNECEDOR", report.obra.fornecedor || "E S Souza construções"],
];
const rightStart = Math.max(3, columnCount - 1);
sheet.getRangeByIndexes(2, rightStart - 1, 2, 2).values = [
  ["PMS", report.filters.pms || report.obra.pms_atual || ""],
  ["PERÍODO", `${report.filters.start || "início"} a ${report.filters.end || "fim"}`],
];

const headerRow = 6;
sheet.getRangeByIndexes(headerRow - 1, 0, 1, columnCount).values = [report.columns.map(x => x[0])];
sheet.getRangeByIndexes(headerRow - 1, 0, 1, columnCount).format = {
  fill: "#17211F",
  font: { name: "Arial", size: 9, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  rowHeight: 30,
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};

const body = report.rows.map(row => report.columns.map(([, key]) => normalize(row[key], key)));
if (body.length) {
  const range = sheet.getRangeByIndexes(headerRow, 0, body.length, columnCount);
  range.values = body;
  range.format = {
    font: { name: "Arial", size: 9, color: "#17211F" },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#9AA8A3" },
  };
  for (let i = 0; i < body.length; i += 2) {
    sheet.getRangeByIndexes(headerRow + i, 0, 1, columnCount).format.fill = "#F5F7F2";
  }
  const moneyKeys = new Set(["valor", "extra", "desconto", "valor_padrao", "valor_ajustado", "total_pagar", "valor_receber"]);
  report.columns.forEach(([, key], index) => {
    if (moneyKeys.has(key)) sheet.getRangeByIndexes(headerRow, index, body.length, 1).format.numberFormat = '"R$" #,##0.00';
  });
}

sheet.freezePanes.freezeRows(headerRow);
sheet.getRange(`A1:${lastColumn}${Math.max(headerRow + body.length, 8)}`).format.verticalAlignment = "center";
for (let index = 0; index < columnCount; index += 1) {
  const key = report.columns[index][1];
  const wide = ["servico", "servico_pms", "descricao", "obs", "observacao_qualidade", "assinatura_encarregado"].includes(key);
  sheet.getRangeByIndexes(0, index, Math.max(headerRow + body.length, 8), 1).format.columnWidth = wide ? 28 : 14;
}
workbook.recalculate();
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
if (errors.ndjson.trim()) throw new Error(`Erros encontrados no Excel: ${errors.ndjson}`);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

function normalize(value, key) {
  if (value === null || value === undefined) return "";
  if (["valor", "extra", "desconto", "valor_padrao", "valor_ajustado", "total_pagar", "valor_receber", "quantidade", "quantidade_m2"].includes(key)) {
    const number = Number(value);
    return Number.isFinite(number) ? number : value;
  }
  return value;
}

function columnName(count) {
  let name = "";
  let n = count;
  while (n > 0) {
    n -= 1;
    name = String.fromCharCode(65 + (n % 26)) + name;
    n = Math.floor(n / 26);
  }
  return name;
}
