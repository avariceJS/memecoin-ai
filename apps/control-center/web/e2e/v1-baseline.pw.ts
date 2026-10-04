import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page, type Request } from "@playwright/test";
import { measureDom, type DomMeasurement } from "./measure";
import { pngSize } from "./png";
import { appRoot, repoRoot } from "./processes";

const ROUTES = ["/", "/calendar", "/research", "/models", "/sources", "/system"] as const;
const OWNERS: Record<(typeof ROUTES)[number], string> = {
  "/": "CC02.2",
  "/calendar": "CC02.3",
  "/research": "CC02.4",
  "/models": "CC02.5",
  "/sources": "CC02.6",
  "/system": "CC02.7",
};

type RouteName = (typeof ROUTES)[number];

function digest(): string {
  return execFileSync("python3", ["apps/control-center/scripts/visual_gate.py", "--digest"], {
    cwd: repoRoot,
    encoding: "utf8",
  }).trim();
}

function snapshotSha(): string {
  const bytes = readFileSync(path.join(appRoot, "published/observatory-v1.json"));
  return createHash("sha256").update(bytes).digest("hex");
}

function allowed(request: Request): boolean {
  const method = request.method();
  let parsed: URL;
  try {
    parsed = new URL(request.url());
  } catch {
    return false;
  }
  if (parsed.protocol === "data:" || parsed.protocol === "blob:") {
    return method === "GET";
  }
  if (parsed.hostname !== "127.0.0.1") {
    return false;
  }
  if (parsed.port === "5173") {
    return method === "GET" || method === "HEAD" || parsed.protocol === "ws:";
  }
  if (parsed.port === "8787") {
    const known = parsed.pathname === "/api/v1/snapshot" || parsed.pathname === "/api/v1/capabilities";
    return known && (method === "GET" || method === "OPTIONS");
  }
  return false;
}

async function openRoute(page: Page, route: RouteName): Promise<void> {
  await page.goto(route, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Загрузка проекции")).toHaveCount(0);
  await expect(page.getByText("RECORDED EVIDENCE").first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

function reportFor(
  route: RouteName,
  viewport: [number, number],
  measurement: DomMeasurement,
  webSha: string,
  shotSha: string,
) {
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
      reason: `Browser DOM на v1: ${metric} положительный. Это дефект страницы, не зелёный baseline.`,
      owner_task: OWNERS[route],
    }));
  const slug = route === "/" ? "overview" : route.slice(1);
  return {
    schema_version: 1 as const,
    source: "browser-dom" as const,
    route,
    viewport,
    web_sha256: webSha,
    snapshot_sha256: snapshotSha(),
    page_overflow_px: measurement.page_overflow_px,
    text_overlap_count: measurement.text_overlap_count,
    card_escape_count: measurement.card_escape_count,
    unintended_clip_count: measurement.unintended_clip_count,
    page_overflow_hidden: measurement.page_overflow_hidden,
    allowed_scroll_containers: measurement.allowed_scroll_containers,
    inspected_regions: measurement.inspected_regions,
    acceptance_scope: "baseline" as const,
    capture_id: `v1-${slug}-${viewport[0]}x${viewport[1]}`,
    screenshot_sha256: shotSha,
    page_findings: findings,
    shell_counts: measurement.shell_counts,
    samples: measurement.samples,
  };
}

// Calendar still characterizes the rejected D48 grid. Overview page overflow was a v1
// defect; CC02.2 is expected to keep it at or under 1px. Do not hide calendar escapes.
test("v1 layout harness воспроизводит overflow и не красит его в green", async ({ page }) => {
  const webSha = digest();
  const seen: string[] = [];
  page.on("request", (request) => {
    if (!allowed(request)) {
      seen.push(`${request.method()} ${request.url()}`);
    }
  });
  const outDir = path.join(appRoot, ".local/tasks/CC02.0/captures");
  mkdirSync(outDir, { recursive: true });
  const summary: Array<{
    route: RouteName;
    viewport: [number, number];
    card_escape_count: number;
    text_overlap_count: number;
    unintended_clip_count: number;
    page_overflow_px: number;
  }> = [];

  for (const route of ROUTES) {
    await page.setViewportSize({ width: 1600, height: 1000 });
    await openRoute(page, route);
    const measurement = await page.evaluate(measureDom);
    const shot = await page.screenshot({
      fullPage: false,
      animations: "disabled",
      caret: "hide",
      scale: "css",
    });
    const size = pngSize(shot);
    expect(size).toEqual({ width: 1600, height: 1000 });
    expect(measurement.page_overflow_hidden).toBe(false);
    expect(measurement.shell_counts.overflow_px).toBeLessThanOrEqual(1);
    expect(measurement.shell_counts.card_escape_count).toBe(0);
    expect(measurement.inspected_regions).toEqual(
      expect.arrayContaining(["sidebar", "header", "main-grid", "shell-containment"]),
    );
    const shotSha = createHash("sha256").update(shot).digest("hex");
    const report = reportFor(route, [1600, 1000], measurement, webSha, shotSha);
    const slug = route === "/" ? "overview" : route.slice(1);
    writeFileSync(path.join(outDir, `${slug}-1600.json`), JSON.stringify(report, null, 2));
    writeFileSync(path.join(outDir, `${slug}-1600.png`), shot);
    summary.push({
      route,
      viewport: [1600, 1000],
      card_escape_count: measurement.card_escape_count,
      text_overlap_count: measurement.text_overlap_count,
      unintended_clip_count: measurement.unintended_clip_count,
      page_overflow_px: measurement.page_overflow_px,
    });
  }

  await page.setViewportSize({ width: 1024, height: 768 });
  await openRoute(page, "/calendar");
  await expect(page.getByText("DESIGN", { exact: true })).toHaveCount(224);
  const narrow = await page.evaluate(measureDom);
  const narrowShot = await page.screenshot({
    fullPage: false,
    animations: "disabled",
    caret: "hide",
    scale: "css",
  });
  expect(pngSize(narrowShot)).toEqual({ width: 1024, height: 768 });
  expect(narrow.page_overflow_hidden).toBe(false);
  expect(narrow.shell_counts.overflow_px).toBeLessThanOrEqual(1);
  expect(narrow.shell_counts.card_escape_count).toBe(0);
  expect(narrow.inspected_regions).toContain("d48-grid");
  expect(narrow.card_escape_count).toBeGreaterThanOrEqual(400);

  const wide = summary.find((item) => item.route === "/calendar" && item.viewport[0] === 1600);
  const overview = summary.find((item) => item.route === "/" && item.viewport[0] === 1600);
  expect(wide?.card_escape_count).toBeGreaterThanOrEqual(400);
  expect(overview?.page_overflow_px).toBeLessThanOrEqual(1);

  writeFileSync(
    path.join(outDir, "summary.json"),
    JSON.stringify(
      {
        acceptance: "NOT_A_VISUAL_PASS",
        note: "Положительные counts — известный дефект web v1. Не обновлять их в ноль правкой CSS внутри CC02.0.",
        web_sha256: webSha,
        rows: [
          ...summary,
          {
            route: "/calendar",
            viewport: [1024, 768],
            card_escape_count: narrow.card_escape_count,
            text_overlap_count: narrow.text_overlap_count,
            unintended_clip_count: narrow.unintended_clip_count,
            page_overflow_px: narrow.page_overflow_px,
          },
        ],
      },
      null,
      2,
    ),
  );
  expect(seen).toEqual([]);
});
