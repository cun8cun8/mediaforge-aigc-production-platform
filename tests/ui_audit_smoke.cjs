const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const baseURL = process.env.MEDIAFORGE_UI_URL || "http://127.0.0.1:8023";
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const projectId = `ui_audit_${Date.now()}`;
    const response = await context.request.post("/projects", {
      data: {
        project_id: projectId,
        title: "审计链界面验收",
        premise: "一次可验证的项目审计记录。",
        genre: "悬疑",
        style: "电影感",
        characters: ["林夏", "周启"],
        duration_seconds: 30,
        budget: 2,
      },
    });
    assert(response.ok(), await response.text());

    await page.goto("/");
    await page.locator(`[data-project-id="${projectId}"]`).waitFor({ state: "visible" });
    await page.locator(`[data-project-id="${projectId}"]`).click();
    await page.getByRole("button", { name: "审计", exact: true }).click();
    await page.locator("#auditIntegrityStatus").waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.getElementById("auditIntegrityButton").disabled);
    await page.locator("#auditIntegrityButton").click();
    await page.waitForFunction(() => document.getElementById("auditIntegrityStatus").textContent.includes("已验证"));
    await page.locator("#auditIntegrityExportButton").click();
    await page.locator("#auditIntegrityLink").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.getElementById("auditAnchorStatus").textContent.includes("未配置"));
    assert.equal(await page.locator("#auditAnchorButton").isDisabled(), true);
    await page.getByRole("button", { name: "运行与账单", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("enterpriseRuntime").textContent.includes("审计链外部锚定"));
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, "audit controls introduced horizontal overflow");
    await fs.mkdir("artifacts/ui-acceptance", { recursive: true });
    await page.screenshot({ path: path.join("artifacts/ui-acceptance", "audit-mobile.png"), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, project_id: projectId, checks: ["audit integrity", "audit anchor", "runtime status", "mobile overflow"] }));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
