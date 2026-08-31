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
  expect(await page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(await page.evaluate(() => document.documentElement.clientWidth));
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

test("requires an explicit scope acknowledgment before applying an official reference pack", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("radio", { name: /常规文档/ })).toBeVisible({ timeout: 10_000 });
  await page.getByLabel("上传待处理 Word 文档").setInputFiles(fixture);
  await page.getByRole("button", { name: "分析结构" }).click();
  await expect(page.getByText(/分析完成：确定性分析；19 个段落/)).toBeVisible();
  const sourceBoundaryAcknowledgment = page.getByRole("checkbox", {
    name: /我已了解这些复杂内容需要在 Word\/WPS 中逐项核对/,
  });
  await expect(page.getByText("7 类复杂内容需人工核对")).toBeVisible();
  await sourceBoundaryAcknowledgment.check();

  await page.getByRole("radio", { name: /南开大学论文 2026 参考/ }).click();
  await expect(page.getByText("参考规则包 · 部分覆盖")).toBeVisible();
  const coverageMatrix = page.getByRole("list", { name: "规范条款覆盖矩阵" });
  await expect(coverageMatrix).toContainText("自动执行");
  await expect(coverageMatrix).toContainText("人工复核");
  await expect(coverageMatrix).toContainText("暂不支持");
  await expect(page.getByText("自动验收证据")).toBeVisible();
  await expect(page.getByText("人工验收清单")).toBeVisible();
  await expect(page.getByRole("link", { name: /南开大学研究生院/ })).toHaveAttribute(
    "href",
    "https://graduate.nankai.edu.cn/2017/0222/c23238a56863/page.htm",
  );
  await expect(page.getByRole("checkbox", { name: /自动结构排版/ })).not.toBeChecked();

  const formatButton = page.getByRole("button", { name: "格式化并验证" });
  await expect(formatButton).toBeDisabled();
  await expect(page.getByRole("button", { name: "只做格式体检" })).toBeEnabled();
  await page.getByRole("checkbox", { name: /我已查看自动、人工和暂不支持条款/ }).check();
  await expect(formatButton).toBeEnabled();

  await page.getByRole("button", { name: "只做格式体检" }).click();
  await expect(page.getByText(/格式体检完成：发现 \d+ 项偏差/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/源文件未修改/)).toBeVisible();

  await formatButton.click();
  await expect(page.getByText("自动排版与格式验证完成；当前文档无需额外拆分正文。"))
    .toBeVisible({ timeout: 30_000 });
  const resultSummary = page.getByLabel("排版结果摘要");
  await expect(resultSummary.getByText("已声明自动条款验证通过")).toBeVisible();
  await expect(resultSummary.getByText("个结构段落待确认")).toBeVisible();
  await expect(resultSummary.getByText("项交付前人工核对")).toBeVisible();
  await expect(resultSummary.getByText("目录规则原样执行")).toBeVisible();
  const deliveryBoundary = resultSummary.getByLabel("规则交付边界");
  await expect(deliveryBoundary.getByText("交付前核对清单（4）")).toBeVisible();
  await expect(deliveryBoundary.getByText("人工复核")).toBeVisible();
  await expect(deliveryBoundary.getByText("暂不支持")).toBeVisible();
  await expect(deliveryBoundary.getByText("人工验收步骤")).toBeVisible();
});

test("saves reusable rule revisions and restores history without duplicate writes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("radio", { name: /常规文档/ })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: "我的规则包" }).click();

  const saveDetails = page.locator(".rule-pack-save");
  if (!(await saveDetails.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await saveDetails.locator("summary").click();
  }
  const uniqueName = `端到端月报规则-${Date.now()}`;
  await page.getByLabel("规则包名称").fill(uniqueName);
  await page.getByLabel("具体适用范围").fill("端到端测试内部月报");
  await page.getByLabel("本次变更说明").fill("创建端到端初始修订");
  await page.getByRole("button", { name: "另存为新规则包" }).click();
  await expect(page.getByText(`已保存“${uniqueName}”修订 1。`)).toBeVisible();
  await expect(page.locator(".rule-pack-history").getByText(uniqueName)).toBeVisible();

  if (!(await saveDetails.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await saveDetails.locator("summary").click();
  }
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

  const portableArtifact = await exported.json();
  const externalName = `跨机导入规则-${Date.now()}`;
  portableArtifact.pack_id = `pack_external_${Date.now()}`;
  portableArtifact.request_id = `external_${Date.now()}`;
  portableArtifact.name = externalName;
  portableArtifact.scope_label = "另一台电脑导出的端到端规则";
  portableArtifact.approval_status = "locally_approved";
  portableArtifact.approval_note = "来源电脑记录为已核对";

  await page.getByText("从另一台电脑导入规则包").click();
  await page.getByLabel("规则包 JSON 文件").setInputFiles({
    name: "external.rule-pack.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(portableArtifact)),
  });
  await page.getByRole("button", { name: "检查文件" }).click();
  const importPreview = page.getByLabel("规则包导入检查结果");
  await expect(importPreview.getByText("结构与摘要通过")).toBeVisible();
  await expect(importPreview.getByText("未验证数字签名")).toBeVisible();
  await expect(importPreview.getByText(/不会随导入继承/)).toBeVisible();
  await page.getByRole("button", { name: "确认导入为草稿" }).click();
  await expect(page.getByText(`已导入“${externalName}”修订 1；状态已重置为草稿，载入前请重新核对。`))
    .toBeVisible();
  await expect(page.locator(".rule-pack-version-note").getByText(/跨机导入自/)).toBeVisible();
  await page.getByRole("button", { name: "载入所选修订" }).click();
  await expect(page.getByText(`已载入“${externalName}”修订 1；尚未修改源文档。`)).toBeVisible();
});

test("completes the local structured formatting workflow and restores it", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "先理解文档，再智能排版" })).toBeVisible();
  await page.getByLabel("上传待处理 Word 文档").setInputFiles(fixture);
  await expect(page.getByText("文档已安全上传，可以开始结构分析。未修改源文件。")).toBeVisible();

  await page.getByRole("button", { name: "分析结构" }).click();
  await expect(page.getByText(/分析完成：确定性分析；19 个段落/)).toBeVisible();
  const processingBoundary = page.getByLabel("文档处理范围预检");
  await expect(processingBoundary.getByText("7 类复杂内容需人工核对")).toBeVisible();
  await expect(page.getByRole("button", { name: "自动排版并验证" })).toBeDisabled();
  await processingBoundary.getByRole("checkbox", {
    name: /我已了解这些复杂内容需要在 Word\/WPS 中逐项核对/,
  }).check();
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
  await expect(resultSummary.getByLabel("源文档处理边界")).toContainText(
    "7 类复杂内容需人工核对",
  );

  await resultSummary.getByRole("button", { name: "查看格式前后对照" }).click();
  const comparison = page.getByRole("dialog", { name: "格式前后对照" });
  await expect(comparison).toBeVisible();
  await expect(comparison.getByText("预览已就绪")).toHaveCount(2);
  await expect(comparison.getByText(/浏览器近似渲染/)).toBeVisible();
  await expect(comparison.locator(".comparison-scroll a[href]")).toHaveCount(0);
  const sourcePreview = comparison.getByLabel("源文件预览滚动区");
  const outputPreview = comparison.getByLabel("已验证输出预览滚动区");
  const sourceRange = await sourcePreview.evaluate((element) => (
    element.scrollHeight - element.clientHeight
  ));
  expect(sourceRange).toBeGreaterThan(0);
  await sourcePreview.evaluate((element) => {
    element.scrollTop = Math.min(320, element.scrollHeight - element.clientHeight);
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await expect.poll(() => outputPreview.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(comparison).toBeVisible();
  const panePositions = await comparison.locator(".comparison-pane").evaluateAll((elements) => (
    elements.map((element) => element.getBoundingClientRect().top)
  ));
  expect(panePositions[1]).toBeGreaterThan(panePositions[0]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(await page.evaluate(() => document.documentElement.clientWidth));
  await comparison.getByRole("button", { name: "关闭对照" }).click();
  await page.setViewportSize({ width: 1280, height: 720 });

  await resultSummary.getByText(/查看具体改动/).click();
  const firstChangeLocator = resultSummary.getByRole("button", { name: /定位到/ }).first();
  await expect(firstChangeLocator).toBeVisible();
  await firstChangeLocator.click();
  await expect(page.locator(".structure-item.change-focus, .structure-table.change-focus")).toBeVisible();
  const auditUrl = await page.getByRole("link", { name: "查看审计" }).getAttribute("href");
  expect(auditUrl).toBeTruthy();
  const audit = await page.request.get(auditUrl!);
  expect(audit.ok()).toBeTruthy();
  const auditPayload = await audit.json();
  expect(auditPayload.validation.valid).toBe(true);
  expect(auditPayload.source_processing_boundary.review_feature_count).toBe(7);

  const jobId = auditUrl?.match(/\/jobs\/([^/]+)\/audit\.json$/)?.[1];
  expect(jobId).toBeTruthy();
  await page.goto(`/jobs?jobId=${jobId}`);
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
