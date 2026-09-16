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
  const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const projectId = `ui_workflow_${Date.now()}`;
    const created = await context.request.post("/projects", {
      data: {
        project_id: projectId,
        title: "阶段编排验收",
        premise: "一位制片人将来自未来的电话拍成短剧。",
        genre: "悬疑",
        style: "电影感",
        characters: ["林夏", "周启"],
        duration_seconds: 30,
        budget: 2,
      },
    });
    assert(created.ok(), await created.text());
    const plan = await context.request.post(`/projects/${projectId}/plan`);
    assert(plan.ok(), await plan.text());

    await page.goto("/");
    const project = page.locator(`[data-project-id="${projectId}"]`);
    assert.equal(await project.count(), 1);
    await project.click();
    await page.locator("#workflowStageStrip").waitFor({ state: "visible" });
    const stages = page.locator("#workflowStageList .workflow-stage");
    assert.equal(await stages.count(), 6);
    assert.equal(await page.locator("#workflowStageSummary").innerText(), "0 / 6 阶段已锁定");

    const lock = page.locator('[data-action="workflow-lock"][data-stage-key="script"]');
    assert.equal(await lock.count(), 1);
    await lock.click();
    await page.waitForFunction(() => document.getElementById("workflowStageSummary").textContent.includes("1 / 6"));
    assert.equal(await page.locator("#workflowStageSummary").innerText(), "1 / 6 阶段已锁定");

    const root = path.resolve("artifacts/ui-acceptance");
    await fs.mkdir(root, { recursive: true });
    await page.screenshot({ path: path.join(root, "workflow-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, "workflow stage strip introduced horizontal overflow");
    await page.screenshot({ path: path.join(root, "workflow-mobile.png"), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({
      passed: true,
      project_id: projectId,
      checks: ["six stages", "stage lock", "mobile overflow"],
    }));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
