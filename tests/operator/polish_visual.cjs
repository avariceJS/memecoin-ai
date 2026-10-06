"use strict";

// Optional acceptance: install playwright and its Chromium, or set
// MEMECOIN_PLAYWRIGHT_MODULE to an installed module's absolute path.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function browserDependency() {
  const moduleName = process.env.MEMECOIN_PLAYWRIGHT_MODULE || "playwright";
  let modulePath;
  try {
    modulePath = require.resolve(moduleName);
  } catch (error) {
    if (error.code !== "MODULE_NOT_FOUND" || process.env.MEMECOIN_PLAYWRIGHT_MODULE) throw error;
    console.error("optional browser acceptance requires the playwright Node module");
    process.exit(77);
  }
  const { chromium } = require(modulePath);
  if (!fs.existsSync(chromium.executablePath())) {
    console.error("optional browser acceptance requires Playwright Chromium (npx playwright install chromium)");
    process.exit(77);
  }
  return chromium;
}

async function inspectProjection(page) {
  assert.equal(await page.locator("#synth").isVisible(), true);
  assert.equal(await page.locator("#act-paper").getAttribute("aria-disabled"), "true");
  assert.notEqual(await page.locator("#act-train").getAttribute("aria-disabled"), "true");
  assert.deepEqual(await page.locator("#cand option").evaluateAll(nodes => nodes.map(n => n.value)), ["cand-synth"]);
  assert.equal(await page.locator("#chart svg").count(), 1);
  await page.locator("#tab-data").click();
  const markets = await page.locator("#markets").innerText();
  for (const name of ["SYN-HOT", "SYN-COLD", "SYN-QUAR"]) assert.ok(markets.includes(name));
  await page.locator("#tab-paper").click();
  const books = await page.locator("#book option").evaluateAll(nodes => nodes.map(n => n.value));
  assert.equal(books.length, 6);
  assert.equal(new Set(books).size, 6);
  for (const [index, value] of books.entries()) {
    await page.locator("#book").selectOption(value);
    const cash = await page.locator("#cash").innerText();
    assert.equal(Number(cash.replace(/[^0-9.]/g, "")), 1001 + index);
  }
  await page.locator("#book").selectOption(books[0]);
  assert.ok((await page.locator("#band").innerText()).includes("-0.0311"));
  assert.ok(!(await page.locator("#decisions").innerText()).includes("SYN-OTHER"));
  await page.locator("#book").selectOption(books[5]);
  assert.ok((await page.locator("#band").innerText()).includes("-0.1200"));
  assert.ok(!(await page.locator("#decisions").innerText()).includes("SYN-BAND"));
  const scores = await page.locator("#forecasts table").first().locator("tbody tr").allTextContents();
  assert.equal(scores.length, 3);
  for (const expected of ["0.00145", "0.01745", "0.00482"]) assert.ok(scores[0].includes(expected));
  assert.ok(!scores[1].includes("1.00000"));
  for (const expected of ["0.01000", "0.02000"]) assert.ok(scores[2].includes(expected));
  await page.locator("#tab-system").click();
  assert.ok((await page.locator("#recovery").innerText()).includes("2"));
  await page.locator("#act-stop-all").click();
  assert.equal(await page.locator("#dialog").isVisible(), true);
  await page.locator("#dialog-cancel").click();
  assert.equal(await page.locator("#dialog").isVisible(), false);
}

async function layout(page) {
  return page.evaluate(() => {
    const label = document.querySelector(".k");
    const card = label.closest(".card");
    const foreground = getComputedStyle(label).color;
    const background = getComputedStyle(card).backgroundColor;
    function luminance(color) {
      const values = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
    }
    const a = luminance(foreground), b = luminance(background);
    return {
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      foreground,
      background,
    };
  });
}

async function main() {
  const chromium = browserDependency();
  if (process.argv.length === 3 && process.argv[2] === "--probe") return;
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    assert.ok(["--real", "--synth", "--saved", "--out"].includes(process.argv[i]));
    assert.ok(process.argv[i + 1]);
    args[process.argv[i].slice(2)] = process.argv[i + 1];
  }
  for (const name of ["real", "synth", "saved"]) {
    const url = new URL(args[name]);
    assert.equal(url.protocol, "http:");
    assert.equal(url.hostname, "127.0.0.1");
  }
  fs.mkdirSync(args.out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const report = { ok: false, data_kind: "synthetic", layouts: [], contrast: { value: Infinity } };
  try {
    for (const name of ["real", "synth", "saved"]) {
      const page = await browser.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(String(error)));
      await page.route("**/*", route => {
        if (new URL(route.request().url()).origin === new URL(args[name]).origin) return route.continue();
        errors.push("unexpected external request: " + route.request().url());
        return route.abort();
      });
      const response = await page.goto(args[name]);
      assert.equal(response.status(), 200);
      await page.waitForFunction(() => document.querySelector("#mode").textContent.includes("idle"));
      assert.ok((await page.locator("#foot").innerText()).includes(new URL(args[name]).port));
      if (name !== "real") await inspectProjection(page);
      for (const width of [1440, 1100, 390]) {
        await page.setViewportSize({ width, height: 900 });
        for (const tab of ["overview", "data", "training", "models", "paper", "system"]) {
          await page.locator("#tab-" + tab).click();
          const measured = await layout(page);
          assert.ok(measured.overflow <= 1, `${name}/${width}/${tab}: overflow ${measured.overflow}`);
          assert.ok(measured.contrast >= 4.5, JSON.stringify(measured));
          report.contrast.value = Math.min(report.contrast.value, measured.contrast);
          report.layouts.push({ name, width, tab, ...measured });
          await page.screenshot({ path: path.join(args.out, `${name}-${tab}-${width}.png`), fullPage: true });
        }
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    report.ok = true;
    fs.writeFileSync(path.join(args.out, "polish-visual.json"), JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
