import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

const fixture = resolve(process.cwd(), "../../tests/fixtures/academic-comprehensive.docx");

test("completes the local structured formatting workflow and restores it", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "先理解文档，再智能排版" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByText("文档已安全上传，可以开始结构分析。未修改源文件。")).toBeVisible();

  await page.getByRole("button", { name: "分析结构" }).click();
  await expect(page.getByText(/分析完成：确定性分析；19 个段落/)).toBeVisible();
  await page
    .getByLabel("修改段落角色：外部资料：ECMA-376")
    .selectOption("blockquote");
  await expect(page.getByText("角色修正已保存。")).toBeVisible();

  await page.getByRole("button", { name: "自动排版并验证" }).click();
  await expect(page.getByText("自动排版与格式验证完成；当前文档无需额外拆分正文。")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("link", { name: "下载格式化 DOCX" })).toBeVisible();
  await expect(page.getByText("格式化结果 · 最佳努力预览")).toBeVisible();
  const auditUrl = await page.getByRole("link", { name: "查看审计" }).getAttribute("href");
  expect(auditUrl).toBeTruthy();
  const audit = await page.request.get(auditUrl!);
  expect(audit.ok()).toBeTruthy();
  expect((await audit.json()).validation.valid).toBe(true);

  await page.reload();
  await expect(page.getByText("已恢复上次本地工作区。")).toBeVisible();
  await expect(page.getByText("输出已生成")).toBeVisible();
});

test("turns plain text into a structured and validated Word document", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "粘贴纯文本" }).click();
  await page.getByLabel("纯文本文件名").fill("智能排版演示.docx");
  await page.getByLabel("粘贴纯文本").fill(
    "# 智能排版演示\n## 研究背景\n这是中英文 mixed content 正文。\n- 保留文档结构",
  );
  await page.getByRole("button", { name: "生成 Word 草稿" }).click();
  await expect(page.getByText("智能排版演示.docx")).toBeVisible();

  await page.getByRole("button", { name: "分析结构" }).click();
  await expect(page.getByText(/分析完成：确定性分析；4 个段落/)).toBeVisible();
  await expect(page.getByText("规则 · 4 段")).toBeVisible();
  await expect(page.getByLabel("修改段落角色：智能排版演示")).toHaveValue("title");
  await expect(page.getByLabel("修改段落角色：研究背景")).toHaveValue("heading_1");
  await expect(page.getByLabel("修改段落角色：保留文档结构")).toHaveValue("list_item");

  await page.getByRole("button", { name: "自动排版并验证" }).click();
  await expect(page.getByText("自动排版与格式验证完成；当前文档无需额外拆分正文。")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("link", { name: "下载格式化 DOCX" })).toBeVisible();
});
