import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { measureDom, type DomMeasurement } from "./measure";
import { pngSize } from "./png";
import { repoRoot } from "./processes";

const ROUTES = ["/", "/calendar", "/research", "/models", "/sources", "/system"] as const;
const OWNERS: Record<(typeof ROUTES)[number], string> = {
  "/": "CC02.2",
  "/calendar": "CC02.3",
  "/research": "CC02.4",
  "/models": "CC02.5",
  "/sources": "CC02.6",
  "/system": "CC02.7",
};
const VIEWPORTS = [
  [1440, 900],
  [1600, 1000],
  [1920, 1080],
  [1024, 768],
  [768, 900],
] as const;

type RouteName = (typeof ROUTES)[number];

function digest(): string {
  return execFileSync("python3", ["apps/control-center/scripts/visual_gate.py", "--digest"], {
    cwd: repoRoot,
    encoding: "utf8",
  }).trim();
}

function slug(route: RouteName): string {
  return route === "/" ? "overview" : route.slice(1);
}

async function openRoute(page: Page, route: RouteName): Promise<void> {
  await page.goto(route, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Загрузка проекции")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
}

test("CC02.1 second shell matrix", async ({ page, browser }) => {
  test.setTimeout(180_000);
  const webSha = digest();
  const browserName = browser.version();
  const out = path.join(repoRoot, "docs/control-center/references/cc02-final/CC02.1/after");
  mkdirSync(out, { recursive: true });
  const captures: unknown[] = [];

  for (const route of ROUTES) {
    for (const [width, height] of VIEWPORTS) {
      await page.setViewportSize({ width, height });
      await openRoute(page, route);
      const measurement: DomMeasurement = await page.evaluate(measureDom);
      const shotBase = {
        animations: "disabled" as const,
        caret: "hide" as const,
        scale: "css" as const,
        clip: { x: 0, y: 0, width, height },
      };
      const shots: Array<{ kind: "viewport" | "fullpage"; image: Buffer }> = [
        { kind: "viewport", image: await page.screenshot(shotBase) },
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
        expect(measurement.page_overflow_hidden).toBe(false);
        expect(measurement.shell_counts.overflow_px).toBeLessThanOrEqual(1);
        expect(measurement.shell_counts.text_overlap_count).toBe(0);
        expect(measurement.shell_counts.card_escape_count).toBe(0);
        expect(measurement.shell_counts.unintended_clip_count).toBe(0);
        expect(measurement.inspected_regions).toEqual(
          expect.arrayContaining(["sidebar", "header", "main-grid", "shell-containment"]),
        );
        const size = pngSize(shot.image);
        expect(size.width).toBe(width);
        if (shot.kind === "viewport") {
          expect(size.height).toBe(height);
        } else {
          expect(size.height).toBeGreaterThanOrEqual(height);
        }
        const sha256 = createHash("sha256").update(shot.image).digest("hex");
        const captureId = `cc021-${slug(route)}-${width}x${height}-${shot.kind}`;
        const pngName = `${captureId}.png`;
        const reportName = `${captureId}.layout.json`;
        const findings = (
          [
            ["page_overflow_px", measurement.page_overflow_px > 1],
            ["text_overlap_count", measurement.text_overlap_count > 0],
            ["card_escape_count", measurement.card_escape_count > 0],
            ["unintended_clip_count", measurement.unintended_clip_count > 0],
          ] as const
        )
          .filter(([, present]) => present)
          .map(([metric]) => ({
            route,
            metric,
            severity: "major" as const,
            reason: `Страничный дефект v1 на ${route}: ${metric}. Оболочка его не закрывает.`,
            owner_task: OWNERS[route],
          }));
        const layout = {
          source: "browser-dom" as const,
          route,
          viewport: [width, height],
          web_sha256: webSha,
          page_overflow_px: measurement.page_overflow_px,
          text_overlap_count: measurement.text_overlap_count,
          card_escape_count: measurement.card_escape_count,
          unintended_clip_count: measurement.unintended_clip_count,
          page_overflow_hidden: false as const,
          allowed_scroll_containers: measurement.allowed_scroll_containers,
          inspected_regions: measurement.inspected_regions,
          acceptance_scope: "shell" as const,
          capture_id: captureId,
          screenshot_sha256: sha256,
          page_findings: findings,
          shell_counts: measurement.shell_counts,
        };
        writeFileSync(path.join(out, pngName), shot.image);
        writeFileSync(path.join(out, reportName), JSON.stringify(layout));
        const reportSha = createHash("sha256").update(JSON.stringify(layout)).digest("hex");
        captures.push({
          route,
          capture_id: captureId,
          path: `docs/control-center/references/cc02-final/CC02.1/after/${pngName}`,
          sha256,
          viewport: [width, height],
          capture: shot.kind,
          dpr: 1,
          zoom: 1,
          web_sha256: webSha,
          captured_at: new Date().toISOString(),
          browser: `Chromium ${browserName}`,
          layout_report: {
            path: `docs/control-center/references/cc02-final/CC02.1/after/${reportName}`,
            sha256: reportSha,
          },
        });
      }
    }
  }

  writeFileSync(
    path.join(out, "manifest.json"),
    JSON.stringify({ web_sha256: webSha, browser: `Chromium ${browserName}`, captures }, null, 2),
  );
});
