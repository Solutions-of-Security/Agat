import { test, expect } from "@playwright/test";
import { zipSync, strToU8 } from "fflate";
import { mkdir } from "node:fs/promises";

// Browser plugin not available. Flow: Knowledge -> choose CSV/XLSX -> upload -> exact-cell preview/error.
function xlsx(formula = false) {
  const main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
  return Buffer.from(zipSync({
    "xl/workbook.xml": strToU8(`<workbook xmlns="${main}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Показатели" sheetId="1" r:id="rId1"/></sheets></workbook>`),
    "xl/_rels/workbook.xml.rels": strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'),
    "xl/worksheets/sheet1.xml": strToU8(`<worksheet xmlns="${main}"><sheetData><row r="2"><c r="B2">${formula ? "<f>100+20</f>" : ""}<v>120</v></c></row></sheetData></worksheet>`),
  }));
}

test("tabular file upload preserves cells in preview and exposes unsupported formula errors", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  const collectionName = `Tabular QA ${testInfo.project.name}`;
  const response = await request.post("/api/v1/knowledge/collections", { data: { name: collectionName, embeddingModel: "fixture-embedding" } });
  expect(response.ok(), await response.text()).toBe(true);
  const collection = await response.json();
  await page.goto("/#knowledge");
  await expect(page).toHaveURL(/#knowledge$/); await expect(page).toHaveTitle(/АГАТ|Agat/i);
  await expect(page.getByRole("heading", { name: collectionName })).toBeVisible();
  await mkdir("/private/tmp/agat-tabular-qa", { recursive: true });
  for (const [name, mimeType, buffer, expected] of [
    ["Rows.csv", "text/csv", Buffer.from("period,requests\n2026-07,100\n2026-08,120\n"), '"CSV"!B3 [text] = "120"'],
    ["Cells.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx(), '"Показатели"!B2 [n] = "120"'],
    ["Formulas.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx(true), "формулы"],
  ]) {
    const article = page.locator("article.knowledge-collection").filter({ has: page.getByRole("heading", { name: collectionName }) });
    await article.getByRole("button", { name: "Документ", exact: true }).click();
    const form = article.locator("form.knowledge-ingest");
    await form.locator('input[type="file"]').setInputFiles({ name, mimeType, buffer });
    await expect(form.getByLabel("Название", { exact: true })).toHaveValue(name);
    await expect(form.getByText(/Файл выбран/)).toBeVisible();
    const uploaded = page.waitForResponse(response => response.url().endsWith(`/collections/${collection.id}/documents/upload`) && response.request().method() === "POST");
    await form.getByRole("button", { name: "Загрузить и индексировать" }).click();
    expect((await uploaded).ok()).toBe(true);
    const preview = page.getByRole("region", { name: "Предпросмотр документа" });
    await expect(preview.getByRole("heading", { name, exact: true })).toBeVisible();
    await expect(preview).toContainText(expected);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await preview.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/private/tmp/agat-tabular-qa/${testInfo.project.name}-${name}.png`, fullPage: false });
    if (name === "Formulas.xlsx") await expect(preview.getByRole("alert")).toContainText("формулы");
    await page.goto("/#knowledge");
    const stored = await (await request.get("/api/v1/knowledge")).json();
    const document = stored.documents.find(item => item.name === name && item.collectionId === collection.id);
    expect(document.status).toBe(name === "Formulas.xlsx" ? "failed" : "pending");
  }
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});
