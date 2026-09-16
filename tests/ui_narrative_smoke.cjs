const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const baseURL = process.env.MEDIAFORGE_UI_URL || "http://127.0.0.1:8024";
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 1440, height: 1000 },
  });
  const root = path.resolve("artifacts/ui-acceptance");
  await fs.mkdir(root, { recursive: true });
  const errors = [];

  try {
    async function api(method, url, data) {
      const response = await context.request.fetch(url, {
        method,
        data,
        timeout: 90_000,
      });
      assert(response.ok(), `${method} ${url}: ${response.status()} ${await response.text()}`);
      return response.json();
    }

    async function refreshAndSelectProject(page, projectId, tabName) {
      await page.locator(`[data-project-id="${projectId}"]`).click();
      await page.getByRole("button", { name: tabName, exact: true }).click();
    }

    const projectId = `narrative_ui_${Date.now()}`;
    await api("POST", "/projects", {
      project_id: projectId,
      title: "故事事件验收",
      premise: "林夏在午夜接到来自未来的电话。",
      genre: "悬疑",
      style: "电影感",
      characters: ["林夏", "周启"],
      duration_seconds: 30,
      budget: 2,
    });
    await api("POST", `/projects/${projectId}/narrative-events`, {
      event_id: "call",
      chapter_number: 1,
      sequence: 1,
      title: "未来来电",
      scene: "公寓客厅",
      summary: "林夏接到未来来电，得知周启将会失踪。",
      characters: ["林夏", "周启"],
      importance: "MAINLINE",
      emotions: ["悬疑"],
      source_locator: "第 1 章，第 4 段",
    });
    await api("POST", `/projects/${projectId}/narrative-events/call/review`, {
      status: "APPROVED",
      expected_revision: 1,
    });

    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await page.locator(`[data-project-id="${projectId}"]`).click();
    await page.getByRole("button", { name: "故事事件", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("narrativeEventList").textContent.includes("未来来电"));
    assert((await page.locator("#narrativeEventList").innerText()).includes("未来来电"));
    assert((await page.locator("#narrativeEventStatus").innerText()).includes("1 / 1"));

    await page.locator("#narrativeChapter").fill("2");
    await page.locator("#narrativeSequence").fill("1");
    await page.locator("#narrativeTitle").fill("天台揭示");
    await page.locator("#narrativeScene").fill("天台");
    await page.locator("#narrativeSummary").fill("周启在天台交出旧照片，并承认电话来自未来。");
    await page.locator("#narrativeCharacters").fill("林夏、周启");
    await page.locator("#narrativeEmotions").fill("揭示、转折");
    await page.locator("#narrativeLocator").fill("第 2 章，第 8 段");
    await page.locator("#saveNarrativeEventButton").click();
    await page.waitForFunction(() => document.getElementById("narrativeEventCount").textContent === "2 条");
    const approve = page.locator('#narrativeEventList [data-action="approve-narrative-event"]');
    assert.equal(await approve.count(), 1);
    await approve.click();
    await page.waitForFunction(() => document.getElementById("narrativeEventStatus").textContent.includes("2 / 2"));
    assert((await page.locator("#narrativeEventList").innerText()).includes("天台揭示"));

    await page.getByRole("button", { name: "剧本场次", exact: true }).click();
    const derivedResponse = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && response.url().includes(`/projects/${projectId}/adaptation-scenes/derive`)
    ));
    await page.locator("#deriveAdaptationScenesButton").click();
    assert((await derivedResponse).ok());
    await refreshAndSelectProject(page, projectId, "剧本场次");
    await page.waitForFunction(() => document.getElementById("adaptationSceneCount").textContent === "2 条");
    assert((await page.locator("#adaptationSceneList").innerText()).includes("未来来电"));
    const scenes = await api("GET", `/projects/${projectId}/adaptation-scenes`);
    for (const scene of scenes.scenes) {
      await api("POST", `/projects/${projectId}/adaptation-scenes/${scene.scene_id}/review`, {
        status: "APPROVED",
        expected_revision: scene.revision,
      });
    }
    await refreshAndSelectProject(page, projectId, "剧本场次");
    await page.waitForFunction(() => document.getElementById("adaptationSceneStatus").textContent.includes("2 个已确认"));
    assert((await page.locator("#adaptationSceneStatus").innerText()).includes("2 个已确认"));
    await page.screenshot({ path: path.join(root, "narrative-desktop.png"), fullPage: true });

    const planned = await api("POST", `/projects/${projectId}/plan`);
    assert(planned.story_bible.narrative_event_context.event_ids.includes("call"));
    assert.equal(planned.story_bible.adaptation_scene_context.scene_ids.length, 2);
    await api("POST", `/projects/${projectId}/shots/${planned.shots[0].shot.shot_id}/enqueue`);
    await refreshAndSelectProject(page, projectId, "故事事件");
    await page.waitForFunction(() => document.getElementById("narrativeEventList").textContent.includes("未来来电"));
    await page.waitForFunction(() => document.getElementById("saveNarrativeEventButton").disabled);
    assert.equal(await page.locator("#saveNarrativeEventButton").isDisabled(), true);
    assert.equal(await page.locator('#narrativeEventList [data-action="edit-narrative-event"]').count(), 0);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#narrativeTabView").scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: path.join(root, "narrative-mobile.png") });
    assert.deepEqual(errors, []);
    const report = {
      passed: true,
      project_id: projectId,
      viewports: [1440, 390],
      checks: [
        "approved narrative event display",
        "event creation and approval",
        "script scene derivation and approved planning input",
        "plan provenance input",
        "media-work lock",
        "mobile overflow",
      ],
      screenshots: root,
    };
    await fs.writeFile(
      path.join(root, "narrative-report.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
