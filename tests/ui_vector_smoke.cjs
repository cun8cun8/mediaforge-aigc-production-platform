const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  });
  const root = path.resolve("artifacts/ui-acceptance");
  await fs.mkdir(root, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(process.env.MEDIAFORGE_UI_URL);
    await page.locator('[data-project-id="vector_browser"]').click();
    await page.getByRole("button", { name: "故事记忆", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("memoryBackend").textContent.includes("PostgreSQL 语义检索"));
    await page.locator("#memoryQuery").fill("雨夜");
    await page.locator("#memorySearchButton").click();
    await page.waitForFunction(() => document.getElementById("memoryResults").textContent.includes("余弦相似度"));
    assert((await page.locator("#memoryResults").innerText()).includes("SHA-256"));
    await page.screenshot({ path: path.join(root, "semantic-desktop.png"), fullPage: true });
    await page.getByRole("button", { name: "运行与账单", exact: true }).click();
    await page.locator("#memoryProbe").click();
    await page.waitForFunction(() => document.getElementById("memoryProbeResult").textContent === "检索连接正常");
    assert((await page.locator("#memoryRuntime").innerText()).includes("fixture-model"));
    for (const width of [390, 768]) {
      await page.setViewportSize({ width, height: 844 });
      await page.locator("#memoryRuntime").scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(root, `semantic-${width}.png`) });
    }
    assert.deepEqual(errors, []);
    const report = { passed: true, backend: "real-pgvector", embedding: "fixture-vectors-not-real-language-model", viewports: [1440, 768, 390], page_errors: errors };
    await fs.writeFile(path.join(root, "semantic-report.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
