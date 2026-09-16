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
  const root = path.resolve("artifacts/ui-acceptance");
  await fs.mkdir(root, { recursive: true });
  const errors = [];
  const external = process.env.MEDIAFORGE_UI_PLANNING_EXTERNAL === "true";
  const prefix = external ? "planning-external" : "planning";
  try {
    async function api(method, url, data) {
      const response = await context.request.fetch(url, { method, data, timeout: 90000 });
      assert(response.ok(), `${url}: ${response.status()} ${await response.text()}`);
      return response.json();
    }
    assert.equal((await api("GET", "/providers/status")).mode, "mock");
    const settings = await api("GET", "/planning/status");
    assert(settings.configured && settings.model_mode === (external ? "external" : "deterministic"), "Unexpected planning mode");
    const projectId = `planning_ui_${Date.now()}`;
    await api("POST", "/projects", {project_id: projectId, title: "分步规划验收", premise: "林夏在午夜接到来自未来的电话。", genre: "悬疑", style: "电影感", characters: ["林夏", "周启"], duration_seconds: 30, budget: 2});
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page.locator(`[data-project-id="${projectId}"]`).click();
    await page.getByRole("button", {name: "分步规划", exact: true}).click();
    await page.locator("#planningStart").click();
    if (external) {
      await page.waitForFunction(() => document.getElementById("planningStatus").textContent.includes("阶段失败"));
      await page.locator("#planningComment").fill("林夏望向窗外的倒影。");
      await page.locator("#planningRevise").click();
    }
    await page.waitForFunction(() => document.getElementById("planningStatus").textContent.includes("等待人工确认"));
    assert(await page.locator("#planButton").isDisabled());
    assert((await page.locator("#nextActionLabel").innerText()).includes("等待人工确认"));
    assert((await page.locator("#emptyState").innerText()).includes("6 个草稿镜头"));
    assert.equal((await api("GET", `/projects/${projectId}`)).shots.length, 0);
    assert.equal(await page.locator("#planningTrace .runtime-row").count(), external ? 5 : 4);
    if (external) assert((await page.locator("#planningDraft").innerText()).includes("林夏望向窗外的倒影。"));
    assert.equal(await page.locator("#planningDraft details").count(), 6);
    await page.locator("#planningDraft details").first().locator("summary").click();
    await page.screenshot({path: path.join(root, `${prefix}-desktop.png`), fullPage: true});
    await page.reload();
    await page.locator(`[data-project-id="${projectId}"]`).click();
    await page.getByRole("button", {name: "分步规划", exact: true}).click();
    await page.waitForFunction(() => document.getElementById("planningStatus").textContent.includes("等待人工确认"));
    await page.locator("#planningComment").fill("角色与镜头参数已确认。");
    assert.equal(await page.locator("#planningRevise").isEnabled(), external, "Only external planners support semantic revision");
    await page.locator("#planningApprove").click();
    await page.waitForFunction(() => document.getElementById("planningStatus").textContent.includes("已应用"));
    await page.waitForFunction(() => !document.getElementById("generateAllButton").disabled);
    const accepted = await api("GET", `/projects/${projectId}`);
    assert.equal(accepted.shots.length, 6);
    assert(accepted.story_bible.planning_run_id);
    assert.equal(accepted.story_bible.planning_trace.length, external ? 6 : 5);
    assert.equal(await page.locator("#planningApprove").isEnabled(), false);
    assert.equal(await page.locator("#generateAllButton").isEnabled(), true);
    assert((await page.locator("#auditLog").innerText()).includes("分步规划草稿已通过人工确认并应用。"));
    for (const width of [390, 768]) {
      await page.setViewportSize({width, height: 844});
      await page.locator("#planningTabView").scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: path.join(root, `${prefix}-${width}.png`)});
    }
    await api("POST", `/projects/${projectId}/shots/${accepted.shots[0].shot.shot_id}/submit`);
    assert.deepEqual(errors, []);
    const report = {passed: true, project_id: projectId, engine: "LangGraph", model_mode: external ? "external-fixture" : "deterministic", viewports: [1440, 768, 390], page_errors: errors,
      checks: ["draft isolation", "persisted stage trace", "generation specs", "reload recovery", "human adoption", "production after approval", "responsive overflow"]};
    if (external) report.checks.push("failed storyboard repaired with review feedback");
    await fs.writeFile(path.join(root, `${prefix}-report.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {console.error(error); process.exitCode = 1;});
