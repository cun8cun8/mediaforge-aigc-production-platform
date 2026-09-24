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
  const errors = [];
  const root = path.resolve("artifacts/ui-acceptance");
  await fs.mkdir(root, { recursive: true });
  const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 1000 } });
  try {
    async function api(method, url, data) {
      const response = await context.request.fetch(url, { method, data, timeout: 90000 });
      assert(response.ok(), `${method} ${url}: ${response.status()} ${await response.text()}`);
      return response.json();
    }
    assert.equal((await api("GET", "/providers/status")).mode, "mock", "UI smoke only runs against Mock");
    const projectId = `ui_acceptance_${Date.now()}`;
    await api("POST", "/projects", { project_id: projectId, title: "页面验收项目", premise: "林夏在午夜接到来自未来的电话。", genre: "悬疑", style: "电影感", characters: ["林夏", "周启"], duration_seconds: 30, budget: 2 });
    const plan = await api("POST", `/projects/${projectId}/plan`);
    const shotId = plan.shots[0].shot.shot_id;
    await api("POST", `/projects/${projectId}/shots/${shotId}/submit`);
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page.waitForFunction(() => {
      const status = document.getElementById("overviewAlerts").textContent.trim();
      return status && status !== "检查中";
    });
    assert.notEqual(await page.locator("#overviewAlerts").innerText(), "检查中");
    await page.locator(`[data-project-id="${projectId}"]`).click();
    await page.getByRole("button", { name: "编排", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("temporalRuntime").textContent.includes("Temporal 服务"));
    assert(await page.locator("#temporalStart").isDisabled(), "Temporal starts remain disabled until the optional runtime is configured");
    await page.screenshot({ path: path.join(root, "temporal-disabled-desktop.png"), fullPage: true });
    await page.getByRole("button", { name: "故事记忆", exact: true }).click();
    await page.locator("#memorySearchButton").waitFor({ state: "visible" });
    await page.locator("#memoryQuery").fill("林夏");
    await page.locator("#memorySearchButton").click();
    await page.waitForFunction(() => document.getElementById("memoryResults").textContent.includes("SHA-256"));
    assert((await page.locator("#memoryResultCount").innerText()) !== "0 条");
    await page.screenshot({ path: path.join(root, "memory-desktop.png"), fullPage: true });
    await page.locator("#memoryQuery").fill("nonexistentuniquephrase");
    await page.locator("#memorySearchButton").click();
    await page.waitForFunction(() => document.getElementById("memoryResultCount").textContent === "0 条");
    await page.getByRole("button", { name: "运行与账单", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("enterpriseRuntime").textContent.includes("状态存储"));
    await page.locator("#enterpriseProbe").click();
    await page.waitForFunction(() => document.getElementById("enterpriseProbeResult").textContent.includes("连接正常"));
    await page.locator("#memoryProbe").click();
    await page.waitForFunction(() => document.getElementById("memoryProbeResult").textContent === "检索连接正常");
    assert((await page.locator("#memoryRuntime").innerText()).includes("SQLite 关键词"));
    assert((await page.locator("#enterpriseRuntime").innerText()).includes("Stripe 结算回调"));
    assert((await page.locator("#enterpriseRuntime").innerText()).includes("SIEM 审计投递"));
    await page.locator("#billingProject").fill(projectId);
    await page.locator("#billingSearch").click();
    await page.waitForFunction((id) => document.getElementById("billingEvents").textContent.includes(id), projectId);
    const downloaded = page.waitForEvent("download");
    await page.locator("#billingDownload").click();
    const usage = JSON.parse(await fs.readFile(await (await downloaded).path(), "utf8"));
    assert.equal(usage.events[0].project_id, projectId);
    await page.screenshot({ path: path.join(root, "enterprise-desktop.png"), fullPage: true });
    await page.locator("#billingCategory").fill("missing_category");
    await page.locator("#billingSearch").click();
    await page.getByText("无匹配记录", { exact: true }).waitFor();
    await page.locator(`[data-shot-card="${shotId}"] .shot-copy`).click();
    const recheck = page.waitForResponse((response) => response.url().endsWith("/review-quality") && response.request().method() === "POST");
    await page.locator("#recheckQualityButton").click();
    assert((await recheck).ok());
    await page.waitForFunction(() => document.getElementById("recheckQualityButton").textContent === "重新质检");
    await page.waitForFunction(() => document.getElementById("qualityEvidence").textContent.includes("证据 SHA256"));
    const playbackStarted = await page.locator("#inspectorMedia video").evaluate(async (video) => {
      video.muted = true;
      await video.play();
      return !video.paused && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA;
    });
    assert(playbackStarted, "generated media should start playback in the browser");
    await page.screenshot({ path: path.join(root, "quality-desktop.png"), fullPage: true });
    await page.getByRole("button", { name: "审计", exact: true }).click();
    await page.locator("#auditIntegrityStatus").waitFor({ state: "visible" });
    await page.locator("#auditIntegrityButton").click();
    await page.waitForFunction(() => document.getElementById("auditIntegrityStatus").textContent.includes("已验证"));
    await page.locator("#auditIntegrityExportButton").click();
    await page.locator("#auditIntegrityLink").waitFor({ state: "visible" });
    await page.getByRole("button", { name: "资产", exact: true }).click();
    await page.locator("#voiceoverText").fill("中文配音输入状态验收。");
    assert(await page.locator("#voiceoverButton").isEnabled());
    for (const width of [390, 768]) {
      await page.setViewportSize({ width, height: 844 });
      await page.getByRole("button", { name: "运行与账单", exact: true }).click();
      await page.waitForFunction(() => document.getElementById("enterpriseRuntime").textContent.includes("状态存储"));
      await page.locator("#enterpriseTabView").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(root, `enterprise-${width}.png`), fullPage: true });
      await page.screenshot({ path: path.join(root, `enterprise-viewport-${width}.png`) });
      const overflow = await page.evaluate(() => ({width: innerWidth, actual: document.documentElement.scrollWidth,
        elements: [...document.querySelectorAll("body *")].filter((element) => {
          const box = element.getBoundingClientRect(); return box.width && (box.right > innerWidth + 1 || box.left < -1);
        }).slice(0, 15).map((element) => ({id: element.id, tag: element.tagName, class: element.className, width: element.getBoundingClientRect().width}))}));
      assert(overflow.actual <= width + 1, `horizontal overflow: ${JSON.stringify(overflow)}`);
    }
    assert.deepEqual(errors, []);
    const report = { passed: true, project_id: projectId, viewports: [1440, 768, 390], page_errors: errors,
      checks: ["Temporal orchestration disabled state", "memory search and empty state", "enterprise probe", "memory backend and probe", "Stripe settlement callback runtime state", "SIEM runtime state", "billing filters and download", "quality recheck response", "video playback", "audit hash-chain verification and export", "voiceover input", "responsive overflow"], screenshots: root };
    await fs.writeFile(path.join(root, "report.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
