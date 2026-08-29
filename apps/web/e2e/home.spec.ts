import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

const fixture = resolve(process.cwd(), "../../tests/fixtures/academic-comprehensive.docx");

test("keeps advanced rules reachable in short desktop and mobile layouts", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("radio", { name: /常规文档/ })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("通用方案 · 非机构合规")).toBeVisible();

  const rulesPanel = page.locator(".rules-panel");
  const advancedRules = page.locator(".advanced-rules");
  const advancedSummary = advancedRules.getByText("高级规则 JSON · 整理方案");

  const desktopMetrics = await rulesPanel.evaluate((element) => ({
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
    scrollHeight: element.scrollHeight,
  }));
  expect(desktopMetrics.overflowY).toBe("auto");
  expect(desktopMetrics.scrollHeight).toBeGreaterThan(desktopMetrics.clientHeight);

  await advancedSummary.scrollIntoViewIfNeeded();
  await expect(advancedSummary).toBeVisible();
  expect(await rulesPanel.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  await advancedSummary.click();
  const editor = page.getByLabel("结构化规则");
  await editor.scrollIntoViewIfNeeded();
  await expect(editor).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.locator(".advanced-rules").scrollIntoViewIfNeeded();
  await expect(page.getByText("高级规则 JSON · 整理方案")).toBeVisible();
  expect(await page.locator(".rules-panel").evaluate((element) => getComputedStyle(element).overflowY))
    .toBe("visible");
});

test("extracts candidate rules from a reference Word without replacing the workspace document", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("radio", { name: /常规文档/ })).toBeVisible({ timeout: 10_000 });

  await page.getByRole("tab", { name: "参考样例提取" }).click();
  await page.getByLabel("上传合格 Word 样例").setInputFiles(fixture);
  await expect(page.getByText(/候选规则已提取：映射/)).toBeVisible();
  await expect(page.getByLabel("样例候选规则摘要")).toBeVisible();
  await expect(page.getByRole("button", { name: "分析结构" })).toBeDisabled();

  const editor = page.getByLabel("结构化规则");
  await expect(editor).not.toHaveValue(/"type": "template"/);
  await page.getByRole("button", { name: "确认采用候选规则" }).click();
  await expect(page.getByText(/已采用“academic-comprehensive.docx”生成的候选规则/)).toBeVisible();
  await expect(editor).toHaveValue(/"type": "template"/);
});

test("saves reusable rule revisions and restores history without duplicate writes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("radio", { name: /常规文档/ })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: "我的规则包" }).click();

  const saveDetails = page.locator(".rule-pack-save");
  if (!(await saveDetails.getAttribute("open"))) await saveDetails.locator("summary").click();
  const uniqueName = `端到端月报规则-${Date.now()}`;
  await page.getByLabel("规则包名称").fill(uniqueName);
  await page.getByLabel("具体适用范围").fill("端到端测试内部月报");
  await page.getByLabel("本次变更说明").fill("创建端到端初始修订");
  await page.getByRole("button", { name: "另存为新规则包" }).click();
  await expect(page.getByText(`已保存“${uniqueName}”修订 1。`)).toBeVisible();
  await expect(page.locator(".rule-pack-history").getByText(uniqueName)).toBeVisible();

  if (!(await saveDetails.getAttribute("open"))) await saveDetails.locator("summary").click();
  await page.getByLabel("本次变更说明").fill("验证第二个不可变修订");
  await page.getByRole("button", { name: "保存到所选包的新修订" }).click();
  await expect(page.getByText(`已创建“${uniqueName}”修订 2。`)).toBeVisible();

  await page.getByLabel("修订版本").selectOption("1");
  await page.getByRole("button", { name: "载入所选修订" }).click();
  await expect(page.getByText(`已载入“${uniqueName}”修订 1；尚未修改源文档。`)).toBeVisible();
  const exportUrl = await page.getByRole("link", { name: "导出 JSON" }).getAttribute("href");
  expect(exportUrl).toBeTruthy();
  const exported = await page.request.get(exportUrl!);
  expect(exported.ok()).toBeTruthy();
  expect((await exported.json()).schema_version).toBe("rule-pack.v1");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "恢复为新修订" }).click();
  await expect(page.getByText(/已从修订 1 恢复为修订 3/)).toBeVisible();
  await expect(page.locator(".rule-pack-version-note").getByText("草稿", { exact: true }))
    .toBeVisible();
});

test("completes the local structured formatting workflow and restores it", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "先理解文档，再智能排版" })).toBeVisible();
  await page.getByLabel("上传待处理 Word 文档").setInputFiles(fixture);
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
  const resultSummary = page.getByLabel("排版结果摘要");
  await expect(resultSummary.getByText("格式验证通过")).toBeVisible();
  await expect(resultSummary.getByText("原文与受保护结构通过")).toBeVisible();
  await expect(resultSummary.getByText("项实际格式调整")).toBeVisible();
  await resultSummary.getByText(/查看具体改动/).click();
  const firstChangeLocator = resultSummary.getByRole("button", { name: /定位到/ }).first();
  await expect(firstChangeLocator).toBeVisible();
  await firstChangeLocator.click();
  await expect(page.locator(".structure-item.change-focus, .structure-table.change-focus")).toBeVisible();
  const auditUrl = await page.getByRole("link", { name: "查看审计" }).getAttribute("href");
  expect(auditUrl).toBeTruthy();
  const audit = await page.request.get(auditUrl!);
  expect(audit.ok()).toBeTruthy();
  expect((await audit.json()).validation.valid).toBe(true);

  const jobId = auditUrl?.match(/\/jobs\/([^/]+)\/audit\.json$/)?.[1];
  expect(jobId).toBeTruthy();
  await page.goto(`/jobs/${jobId}`);
  await expect(page.getByRole("heading", { name: "任务状态" })).toBeVisible();
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  await expect(page.getByLabel("排版结果摘要").getByText("格式验证通过")).toBeVisible();

  await page.goto("/");
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
