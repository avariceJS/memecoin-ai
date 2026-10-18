import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { measureDom } from "./measure";
import { pngSize } from "./png";
import { repoRoot } from "./processes";

const VIEWPORTS = [
  [1440, 900],
  [1600, 1000],
  [1920, 1080],
  [1024, 768],
] as const;

function digest(): string {
  return execFileSync("python3", ["apps/control-center/scripts/visual_gate.py", "--digest"], {
    cwd: repoRoot,
    encoding: "utf8",
  }).trim();
}

test("CC02.6 sources matrix", async ({ page, browser }) => {
  test.setTimeout(120_000);
  const webSha = digest();
  const browserName = browser.version();
  const out = path.join(repoRoot, "docs/control-center/references/cc02-final/CC02.6/after");
  mkdirSync(out, { recursive: true });
  const captures: unknown[] = [];

  for (const [width, height] of VIEWPORTS) {
    await page.setViewportSize({ width, height });
    await page.goto("/sources", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Источник → квалификация → проекция" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const measurement = await page.evaluate(measureDom);
    expect(measurement.page_overflow_hidden, JSON.stringify(measurement.samples)).toBe(false);
    expect(measurement.page_overflow_px, JSON.stringify(measurement.samples)).toBeLessThanOrEqual(1);
    expect(measurement.text_overlap_count, JSON.stringify(measurement.samples)).toBe(0);
    expect(measurement.card_escape_count, JSON.stringify(measurement.samples)).toBe(0);
    expect(measurement.unintended_clip_count, JSON.stringify(measurement.samples)).toBe(0);
    expect(measurement.inspected_regions).toEqual(
      expect.arrayContaining([
        "sidebar",
        "header",
        "main-grid",
        "shell-containment",
        "source-blockers",
        "pool-ids",
      ]),
    );
    const shotBase = {
      animations: "disabled" as const,
      caret: "hide" as const,
      scale: "css" as const,
    };
    const shots: Array<{ kind: "viewport" | "fullpage"; image: Buffer }> = [
      {
        kind: "viewport",
        image: await page.screenshot({ ...shotBase, clip: { x: 0, y: 0, width, height } }),
      },
    ];
    if (width === 1600 && height === 1000) {
      const fullHeight = await page.evaluate(() =>
        Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, 1000),
      );
      shots.push({
        kind: "fullpage",
        image: await page.screenshot({
          ...shotBase,
          clip: { x: 0, y: 0, width, height: fullHeight },
        }),
      });
    }
    for (const shot of shots) {
      const size = pngSize(shot.image);
      expect(size.width).toBe(width);
      if (shot.kind === "viewport") expect(size.height).toBe(height);
      else expect(size.height).toBeGreaterThanOrEqual(height);
      const sha256 = createHash("sha256").update(shot.image).digest("hex");
      const captureId = `cc026-sources-${width}x${height}-${shot.kind}`;
      const layout = {
        source: "browser-dom" as const,
        route: "/sources",
        viewport: [width, height],
        web_sha256: webSha,
        page_overflow_px: measurement.page_overflow_px,
        text_overlap_count: 0,
        card_escape_count: 0,
        unintended_clip_count: 0,
        page_overflow_hidden: false as const,
        allowed_scroll_containers: measurement.allowed_scroll_containers,
        inspected_regions: measurement.inspected_regions,
        acceptance_scope: "page" as const,
        capture_id: captureId,
        screenshot_sha256: sha256,
        page_findings: [],
        shell_counts: measurement.shell_counts,
      };
      writeFileSync(path.join(out, `${captureId}.png`), shot.image);
      const reportBody = JSON.stringify(layout);
      writeFileSync(path.join(out, `${captureId}.layout.json`), reportBody);
      captures.push({
        route: "/sources",
        capture_id: captureId,
        path: `docs/control-center/references/cc02-final/CC02.6/after/${captureId}.png`,
        sha256,
        viewport: [width, height],
        capture: shot.kind,
        dpr: 1,
        zoom: 1,
        web_sha256: webSha,
        captured_at: new Date().toISOString(),
        browser: `Chromium ${browserName}`,
        layout_report: {
          path: `docs/control-center/references/cc02-final/CC02.6/after/${captureId}.layout.json`,
          sha256: createHash("sha256").update(reportBody).digest("hex"),
        },
      });
    }
  }

  writeFileSync(
    path.join(out, "manifest.json"),
    JSON.stringify({ web_sha256: webSha, browser: `Chromium ${browserName}`, captures }, null, 2),
  );
});
