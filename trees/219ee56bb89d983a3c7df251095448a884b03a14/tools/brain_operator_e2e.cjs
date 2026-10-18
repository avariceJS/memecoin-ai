#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const REPO = path.resolve(__dirname, "..");
const PLAYWRIGHT = path.join(
  "/Users/root1/.codex/worktrees/ec89/memecoin-ai/apps/control-center/web/node_modules/playwright",
);
const PYTHON = path.join(REPO, ".venv/bin/python");
let activeFixture = null;
const FIXTURE = path.join(REPO, "tests/operator/browser_fixture.py");
const WIDTHS = [
  { width: 1440, height: 1000 },
  { width: 1100, height: 900 },
  { width: 390, height: 844 },
];

function parseArgs(argv) {
  const args = {
    fixture: false,
    baseUrl: "",
    out: path.join(REPO, "work/brain-b13/delegation"),
    port: 18821,
    label: "",
    round: 0,
    final: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--fixture-workflow") args.fixture = true;
    else if (token === "--base-url") args.baseUrl = argv[++i] || "";
    else if (token === "--out") args.out = path.resolve(argv[++i] || "");
    else if (token === "--port") args.port = Number(argv[++i]);
    else if (token === "--label") args.label = argv[++i] || "";
    else if (token === "--round") args.round = Number(argv[++i]);
    else if (token === "--final") args.final = true;
    else throw new Error(`unknown argument ${token}`);
  }
  if (args.round && args.round !== 2) throw new Error("only --round 2 is implemented");
  if (args.round === 2 || args.final) {
    args.fixture = true;
    if (args.baseUrl) throw new Error("fixture acceptance ignores live --base-url");
  }
  if (args.fixture && args.baseUrl) {
    throw new Error("--fixture-workflow ignores live --base-url; pass only one mode");
  }
  if (!args.fixture && !args.baseUrl) throw new Error("pass --base-url or --fixture-workflow");
  if (!Number.isInteger(args.port) || args.port < 18800) {
    throw new Error("--port must be an integer >= 18800");
  }
  return args;
}

function stamp(file) {
  try {
    const stat = fs.statSync(file);
    return { path: file, mtime_ms: stat.mtimeMs, size: stat.size };
  } catch (err) {
    return { path: file, missing: true, error: String(err.message || err) };
  }
}

async function launchBrowser() {
  const { chromium } = require(PLAYWRIGHT);
  const browser = await chromium.launch({ headless: true });
  return { chromium, browser };
}

function attachErrors(page, bucket, gate) {
  page.on("pageerror", (err) => {
    if (gate.ignore) return;
    bucket.push({ type: "pageerror", text: String(err) });
  });
  page.on("console", (msg) => {
    if (msg.type() !== "error" || gate.ignore) return;
    const text = msg.text();
    if (/favicon|net::ERR_FAILED/.test(text) && gate.allowNetwork) return;
    bucket.push({ type: "console", text });
  });
}

async function catalog(page) {
  return page.evaluate(() => {
    const nodes = [...document.querySelectorAll("button, a, select, input, h1, h2, [role]")];
    return nodes.map((el) => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role"),
      name: (el.getAttribute("aria-label") || el.innerText || el.id || "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 180),
      disabled: Boolean(el.disabled) || el.getAttribute("aria-disabled"),
    }));
  });
}

async function button(page, name) {
  const exact = page.getByRole("button", { name, exact: true });
  if ((await exact.count()) >= 1) return exact.first();
  const found = await catalog(page);
  throw new Error(`no button ${JSON.stringify(name)}; catalog=${JSON.stringify(found)}`);
}

async function labeledValue(page, label) {
  return page.evaluate((wanted) => {
    const nodes = [...document.querySelectorAll("div, span, small, h2, h3")];
    const labelNode = nodes.find((node) => node.textContent.trim() === wanted && node.children.length === 0);
    if (!labelNode || !labelNode.parentElement) return null;
    const value = [...labelNode.parentElement.children].find((child) => child !== labelNode);
    return value ? value.textContent.trim() : null;
  }, label);
}

async function cardText(page, heading) {
  const card = page.locator("article").filter({
    has: page.getByRole("heading", { name: heading, exact: true }),
  });
  await card.first().waitFor({ state: "visible", timeout: 10000 });
  return card.first().evaluate((article) => {
    const parts = [...article.children].filter((node) => node.tagName !== "H2");
    return parts.map((node) => node.innerText).join("\n");
  });
}

async function showTab(page, name) {
  const tab = page.getByRole("tab", { name, exact: true });
  if ((await tab.count()) === 0) return false;
  await tab.first().click();
  return true;
}

async function modeText(page) {
  return page.evaluate(() => {
    const header = document.querySelector("header");
    const span = header && header.querySelector("span");
    return span ? span.textContent.trim() : "";
  });
}

async function shot(page, outDir, stem) {
  const file = path.join(outDir, `${stem}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return path.basename(file);
}

async function measure(page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const pageOverflow = root.scrollWidth - root.clientWidth;
    function contained(el) {
      let node = el.parentElement;
      while (node && node !== document.body) {
        const style = getComputedStyle(node);
        if (style.overflowX === "auto" || style.overflowX === "scroll" || style.overflowX === "hidden") {
          return true;
        }
        node = node.parentElement;
      }
      return false;
    }
    const offenders = [];
    for (const el of document.querySelectorAll("body *")) {
      const rect = el.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) continue;
      if (rect.right <= root.clientWidth + 1 && rect.left >= -1) continue;
      if (contained(el)) continue;
      offenders.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || "",
        className: String(el.className).slice(0, 80),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      });
      if (offenders.length >= 8) break;
    }
    const tables = [...document.querySelectorAll("table")].map((table) => ({
      scrollWidth: table.scrollWidth,
      clientWidth: table.clientWidth,
      contained: contained(table),
    }));
    const chart = document.querySelector("svg.chart");
    const box = chart ? chart.getBoundingClientRect() : null;
    const styles = getComputedStyle(document.body);
    const label = document.querySelector(".k");
    const labelStyle = label ? getComputedStyle(label) : null;
    const card = label ? label.closest(".card") : null;
    const cardStyle = card ? getComputedStyle(card) : null;
    function lum(color) {
      const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!match) return null;
      const parts = [match[1], match[2], match[3]].map((item) => {
        const channel = Number(item) / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2];
    }
    let contrast = null;
    if (labelStyle && cardStyle) {
      const fg = lum(labelStyle.color);
      const bg = lum(cardStyle.backgroundColor);
      if (fg != null && bg != null) {
        const hi = Math.max(fg, bg);
        const lo = Math.min(fg, bg);
        contrast = (hi + 0.05) / (lo + 0.05);
      }
    }
    return {
      viewport: root.clientWidth,
      pageOverflow,
      offenders,
      tables,
      chart: chart
        ? {
            width: Math.round(box.width),
            height: Math.round(box.height),
            texts: chart.querySelectorAll("text").length,
            label: chart.getAttribute("aria-label"),
          }
        : null,
      contrast,
      labelColor: labelStyle ? labelStyle.color : null,
      cardColor: cardStyle ? cardStyle.backgroundColor : null,
      bodySize: styles.fontSize,
    };
  });
}

async function auditWidths(page, outDir, stem, screenshots, layouts) {
  const current = page.viewportSize();
  for (const viewport of WIDTHS) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(150);
    const layout = await measure(page);
    layout.width = viewport.width;
    layout.stem = stem;
    layouts.push(layout);
    screenshots.push(await shot(page, outDir, `browser-${stem}-${viewport.width}`));
  }
  if (current) await page.setViewportSize(current);
}

function startFixture(port, root, extra = []) {
  const args = [FIXTURE, "--port", String(port), ...extra];
  if (root) args.push("--root", root);
  const child = spawn(PYTHON, args, {
    cwd: REPO,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  activeFixture = child;
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  const ready = new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error(`fixture startup timeout\n${stderr}`)), 20000);
    const onData = (chunk) => {
      buffer += chunk.toString();
      const idx = buffer.indexOf("\n");
      if (idx < 0) return;
      clearTimeout(timer);
      child.stdout.off("data", onData);
      try {
        resolve(JSON.parse(buffer.slice(0, idx)));
      } catch (err) {
        reject(err);
      }
    };
    child.stdout.on("data", onData);
    child.on("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`fixture exited ${code} before ready\n${stderr}`));
    });
  });
  return { child, ready, stderr: () => stderr };
}

function stopFixture(child) {
  if (!child || child.killed || child.exitCode != null) return Promise.resolve();
  return new Promise((resolve) => {
    child.once("exit", () => resolve());
    child.kill("SIGTERM");
    setTimeout(() => {
      if (child.exitCode == null) child.kill("SIGKILL");
    }, 4000).unref();
  });
}

async function poll(read, accept, timeoutMs) {
  const started = Date.now();
  let last = "";
  while (Date.now() - started < timeoutMs) {
    last = await read();
    if (accept(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`timeout after ${timeoutMs}ms: ${String(last).slice(0, 600)}`);
}

async function runFixture(args) {
  const label = args.label || "fixture";
  if (label === "fixture" && fs.existsSync(path.join(args.out, "browser-fixture-acceptance.json"))) {
    throw new Error("browser-fixture-acceptance.json is immutable; use --round 2");
  }
  const screenshots = [];
  const layouts = [];
  const checks = [];
  const jsErrors = [];
  const fixes = [];
  const gate = { ignore: false, allowNetwork: false };
  let child = null;
  let browser = null;
  let blocked = false;
  const record = (id, status, detail, fix) => {
    checks.push({ id, status, detail: detail ?? null, fix: fix || null });
    if (status === "fail" && fix) fixes.push({ id, fix });
  };
  async function step(id, fn, { required = false, fix = null } = {}) {
    if (blocked && required) {
      record(id, "blocked", "earlier required step failed", fix);
      return null;
    }
    try {
      const detail = await fn();
      record(id, "pass", detail ?? null, null);
      return detail;
    } catch (err) {
      const detail = String(err && err.stack ? err.stack : err);
      record(id, "fail", detail, fix);
      if (required) blocked = true;
      return null;
    }
  }

  fs.mkdirSync(args.out, { recursive: true });
  const started = startFixture(args.port, null);
  child = started.child;
  let info;
  try {
    info = await started.ready;
  } catch (err) {
    record("fixture_server", "fail", String(err), "Operator web/runtime must import before browser acceptance.");
    return finish(args, label, {
      status: "fail",
      data_kind: "synthetic",
      checks,
      screenshots,
      layouts,
      js_errors: jsErrors,
      ui_fixes: fixes,
      fixture: null,
    });
  }
  record("fixture_server", "pass", info);

  const launched = await launchBrowser();
  browser = launched.browser;
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    locale: "ru-RU",
    colorScheme: "dark",
  });
  page.setDefaultTimeout(15000);
  attachErrors(page, jsErrors, gate);
  const base = info.url;

  let delayed = false;
  await page.route("**/api/status", async (route) => {
    try {
      if (!delayed) {
        delayed = true;
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
      await route.continue();
    } catch (err) {
      const text = String(err.message || err);
      if (!/already handled|closed|disposed/.test(text)) jsErrors.push({ type: "route", text });
    }
  });
  await step("loading_state", async () => {
    await page.goto(base, { waitUntil: "domcontentloaded" });
    const toast = await page.getByRole("status").innerText();
    if (!/Загрузка/.test(toast)) throw new Error(`toast=${toast}`);
    screenshots.push(await shot(page, args.out, "browser-fixture-loading-1440"));
    return { toast };
  }, { fix: "Оставить видимый статус загрузки, пока первый /api/status не вернулся." });
  await step("status_rendered", async () => {
    await poll(async () => page.getByRole("status").innerText(), (text) => /rev /.test(text), 10000);
    const mode = await modeText(page);
    if (!mode.includes("idle")) throw new Error(`mode=${mode}`);
    return { mode };
  }, { required: true });

  await step("csrf_missing_token", async () => {
    const before = await modeText(page);
    const result = await page.evaluate(async () => {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: "csrfprobe01", action: "pause", params: {} }),
      });
      return { status: response.status, body: await response.json() };
    });
    const after = await modeText(page);
    if (result.status !== 403 || result.body.reason !== "bad_token") throw new Error(JSON.stringify(result));
    if (after !== before) throw new Error(`mode changed ${before} -> ${after}`);
    if (JSON.stringify(result).includes("Traceback") || JSON.stringify(result).includes("/Users/")) {
      throw new Error("csrf body leaked traceback or path");
    }
    return result;
  }, { required: true, fix: "POST без X-Operator-Token должен оставаться 403 bad_token и не менять режим." });

  await step("csrf_bad_origin", async () => {
    const token = await page.locator('meta[name="operator-token"]').getAttribute("content");
    const response = await page.request.post(`${base}/api/command`, {
      headers: {
        Origin: "http://evil.example",
        "Content-Type": "application/json",
        "X-Operator-Token": token || "",
      },
      data: { request_id: "csrfprobe02", action: "pause", params: {} },
    });
    const body = await response.json();
    if (response.status() !== 403) throw new Error(JSON.stringify({ status: response.status(), body }));
    return { status: response.status(), reason: body.reason };
  });

  await step("dom_controls", async () => {
    const names = ["Сбор", "Снимок", "Обучить", "Paper exam", "Авто", "Пауза", "Продолжить", "Стоп", "Стоп всё"];
    for (const name of names) await button(page, name);
    const tabs = ["Обзор", "Данные", "Обучение", "Модели", "Paper", "Система"];
    for (const name of tabs) {
      if ((await page.getByRole("tab", { name, exact: true }).count()) < 1) throw new Error(`missing tab ${name}`);
    }
    const profile = page.getByLabel("Профиль сбора");
    if ((await profile.count()) < 1) throw new Error("profile combobox missing");
    return { buttons: names, tabs, catalog: await catalog(page) };
  }, { required: true });

  await step("honesty_banner", async () => {
    const text = await page.locator("body").innerText();
    if (!text.includes("не доказывает edge") || !text.includes("не MPC")) throw new Error("banner missing");
    return { present: true };
  });

  await step("empty_states", async () => {
    await showTab(page, "Данные");
    const snapshots = await cardText(page, "Снимки");
    await showTab(page, "Paper");
    const decisions = await cardText(page, "Решения WAIT / ENTER / HOLD / EXIT");
    await showTab(page, "Модели");
    const models = await cardText(page, "Кандидаты и validation");
    await showTab(page, "Обзор");
    const chart = await cardText(page, "Рост корпуса");
    if (!/Пока пусто/.test(snapshots) || !/Пока пусто/.test(decisions) || !/Пока пусто/.test(models)) {
      throw new Error(JSON.stringify({ snapshots, decisions, models }));
    }
    const sparse = /Одно реальное наблюдение|Нет ряда/.test(chart);
    const notGrown = /80\s*→\s*80/.test(chart);
    if (!sparse && !notGrown) throw new Error(`chart was not sparse: ${chart}`);
    screenshots.push(await shot(page, args.out, "browser-fixture-empty-1440"));
    return { snapshots: snapshots.slice(0, 180), chart: chart.slice(0, 180) };
  }, { required: true });

  await auditWidths(page, args.out, "fixture-empty", screenshots, layouts);

  await step("train_without_snapshot", async () => {
    await button(page, "Обучить").then((node) => node.click());
    const toast = await page.getByRole("status").innerText();
    if (!/Снимок не найден/.test(toast)) throw new Error(`toast=${toast}`);
    return { toast };
  });

  await step("paper_without_candidate", async () => {
    await button(page, "Paper exam").then((node) => node.click());
    const toast = await page.getByRole("status").innerText();
    if (!/Кандидат не найден/.test(toast)) throw new Error(`toast=${toast}`);
    return { toast };
  });

  await step("collect_failure_visible", async () => {
    await button(page, "Сбор").then((node) => node.click());
    await showTab(page, "Обучение");
    const jobs = await poll(
      async () => cardText(page, "Задачи"),
      (text) => /failed/.test(text) && /synthetic_failure/.test(text),
      15000,
    );
    const toast = await page.getByRole("status").innerText();
    screenshots.push(await shot(page, args.out, "browser-fixture-collect-failed-1440"));
    return { jobs: jobs.slice(0, 400), toast };
  }, {
    required: true,
    fix: "Провал задачи должен оставаться видимым в таблице задач. Тост команды не должен затираться следующим опросом, если причина ещё не продублирована на текущей вкладке.",
  });

  await step("command_toast_persists", async () => {
    const toast = await page.getByRole("status").innerText();
    if (/synthetic_failure|Ошибка адаптера|Неполный/.test(toast)) return { toast, persisted: true };
    throw new Error(`toast was replaced: ${toast}`);
  }, {
    fix: "render() переписывает #toast на каждом опросе и стирает результат команды. Держать последнюю причину отдельно от строки revision/phase.",
  });

  await step("collect_growth_visible", async () => {
    await showTab(page, "Обзор");
    await button(page, "Сбор").then((node) => node.click());
    const matured = await poll(async () => labeledValue(page, "Matured"), (text) => text === "240", 15000);
    const chart = await poll(
      async () => cardText(page, "Рост корпуса"),
      (text) => /240/.test(text) && /80/.test(text),
      15000,
    );
    const svg = await page.getByRole("img", { name: "Рост matured" }).count();
    if (svg < 1) throw new Error(`chart text without svg: ${chart}`);
    screenshots.push(await shot(page, args.out, "browser-fixture-growth-1440"));
    return { matured, chart: chart.slice(0, 240) };
  }, { required: true });

  await step("snapshot_visible", async () => {
    await button(page, "Снимок").then((node) => node.click());
    await showTab(page, "Данные");
    const text = await poll(async () => cardText(page, "Снимки"), (value) => /synthds1/.test(value), 15000);
    const select = page.getByLabel("Снимок для обучения");
    await select.selectOption("synthds1");
    screenshots.push(await shot(page, args.out, "browser-fixture-snapshot-1440"));
    return { text: text.slice(0, 240) };
  }, { required: true });

  await step("growth_gate_reason", async () => {
    await showTab(page, "Обзор");
    const text = await poll(
      async () => cardText(page, "Следующий шаг"),
      (value) => /Мало новых matured/.test(value),
      10000,
    );
    return { text: text.slice(0, 500) };
  }, { required: true });

  let runningFit = "";
  await step("train_running_and_candidate", async () => {
    await button(page, "Обучить").then((node) => node.click());
    await showTab(page, "Обучение");
    let sawRunning = false;
    const started = Date.now();
    while (Date.now() - started < 20000) {
      const fit = await cardText(page, "Текущий fit");
      const jobs = await cardText(page, "Задачи");
      if (/running/.test(fit) || /running/.test(jobs)) {
        sawRunning = true;
        runningFit = fit;
        screenshots.push(await shot(page, args.out, "browser-fixture-train-running-1440"));
        break;
      }
      await page.waitForTimeout(400);
    }
    if (!sawRunning) throw new Error("running state was not painted");
    await showTab(page, "Модели");
    const models = await poll(
      async () => cardText(page, "Кандидаты и validation"),
      (value) => /synthcand1/.test(value) && /synthetic/.test(value),
      20000,
    );
    screenshots.push(await shot(page, args.out, "browser-fixture-candidate-1440"));
    return { models: models.slice(0, 300), runningFit: runningFit.slice(0, 300) };
  }, { required: true });

  await step("train_progress_fields", async () => {
    if (!runningFit) throw new Error("running fit text was not captured");
    if (/—\s*\/\s*—/.test(runningFit) || !/\d+\s*\/\s*\d+/.test(runningFit)) {
      throw new Error(runningFit.slice(0, 400));
    }
    return { fit: runningFit.slice(0, 300) };
  }, {
    fix: "Пока train в статусе running, карточка «Текущий fit» должна показывать шаг и checkpoint. Сейчас там «— / —»: прогресс пишется только из maintain раз в 15 с и короткий fit его не застаёт.",
  });

  const paperSamples = [];
  await step("paper_action_transitions", async () => {
    const candidate = page.getByLabel("Кандидат для paper");
    await candidate.selectOption("synthcand1");
    await button(page, "Paper exam").then((node) => node.click());
    await showTab(page, "Paper");
    const stages = ["WAIT", "ENTER", "HOLD", "EXIT"];
    const started = Date.now();
    let shotStage = "";
    while (Date.now() - started < 48000) {
      const text = await cardText(page, "Решения WAIT / ENTER / HOLD / EXIT");
      const book = await cardText(page, "Позиции и сделки");
      const present = stages.filter((stage) => new RegExp(`\\b${stage}\\b`).test(text));
      const row = { t_ms: Date.now() - started, present, book: book.slice(0, 180) };
      const prev = paperSamples[paperSamples.length - 1];
      if (!prev || prev.present.join() !== present.join()) {
        paperSamples.push(row);
        const key = present.join("-") || "none";
        if (key !== shotStage && present.length) {
          shotStage = key;
          screenshots.push(await shot(page, args.out, `browser-fixture-paper-${key.toLowerCase()}-1440`));
        }
      }
      if (present.includes("EXIT")) break;
      await page.waitForTimeout(700);
    }
    const seen = (stage, later) =>
      paperSamples.some((sample) => sample.present.includes(stage) && !sample.present.includes(later));
    if (!seen("WAIT", "ENTER") || !seen("ENTER", "HOLD") || !seen("HOLD", "EXIT")) {
      throw new Error(`transitions skipped ${JSON.stringify(paperSamples)}`);
    }
    if (!paperSamples.some((sample) => sample.present.includes("EXIT"))) {
      throw new Error("EXIT was not visible");
    }
    const exitSample = [...paperSamples].reverse().find((sample) => sample.present.includes("EXIT"));
    const book = await cardText(page, "Позиции и сделки");
    if (!/-1\.25000000/.test(book)) throw new Error(`trade not visible: ${book}`);
    return { samples: paperSamples, exit: exitSample };
  }, {
    required: true,
    fix: "Таблица решений должна показывать смену WAIT → ENTER → HOLD → EXIT отдельными обновлениями, с fixed 15m у сделки.",
  });

  await auditWidths(page, args.out, "fixture-paper", screenshots, layouts);

  await step("chart_axes", async () => {
    await showTab(page, "Обзор");
    const layout = await measure(page);
    if (!layout.chart) throw new Error("svg chart missing after growth");
    if (layout.chart.width < 80 || layout.chart.height < 40) throw new Error(JSON.stringify(layout.chart));
    if (layout.chart.texts > 0) return layout.chart;
    throw new Error(`chart has no axis text: ${JSON.stringify(layout.chart)}`);
  }, {
    fix: "В SVG роста корпуса нет оси, шкалы и подписей времени. Добавить min/max matured и две временные метки, не только линию и подпись под графиком.",
  });

  let pausedFlags = null;
  await step("auto_blocked_then_pause_resume", async () => {
    await showTab(page, "Обзор");
    await button(page, "Авто").then((node) => node.click());
    const blockedText = await poll(
      async () => `${await modeText(page)}\n${await cardText(page, "Следующий шаг")}`,
      (value) => /auto/.test(value) && /Мало новых matured/.test(value),
      10000,
    );
    screenshots.push(await shot(page, args.out, "browser-fixture-auto-blocked-1440"));
    await button(page, "Пауза").then((node) => node.click());
    const paused = await poll(async () => modeText(page), (value) => /paused/.test(value), 10000);
    const pausedReason = await cardText(page, "Следующий шаг");
    if (!/Пауза/.test(pausedReason)) throw new Error(pausedReason);
    pausedFlags = await controlFlags(page);
    const maturedBefore = await labeledValue(page, "Matured");
    const pausedApi = await postCommand(
      page,
      "collect",
      { profile: "balanced" },
      "pausecollect01",
    );
    if (pausedApi.status !== 409 || pausedApi.body.reason !== "paused") {
      throw new Error(`paused collect API ${JSON.stringify(pausedApi)}`);
    }
    const maturedAfter = await labeledValue(page, "Matured");
    if (maturedAfter !== maturedBefore) {
      throw new Error(`paused collect changed ${maturedBefore} -> ${maturedAfter}`);
    }
    screenshots.push(await shot(page, args.out, "browser-fixture-paused-1440"));
    await button(page, "Продолжить").then((node) => node.click());
    const resumed = await poll(async () => modeText(page), (value) => /auto/.test(value), 10000);
    screenshots.push(await shot(page, args.out, "browser-fixture-resumed-1440"));
    return { blockedText: blockedText.slice(0, 400), paused, resumed, flags: pausedFlags };
  }, { required: true });

  await step("paused_controls_disabled", async () => {
    if (!pausedFlags) throw new Error("pause flags were not captured");
    const disabledOk = pausedFlags.every(
      (item) => item.disabled === true && item.ariaDisabled === "true",
    );
    if (!disabledOk) throw new Error(`paused buttons need disabled and aria-disabled ${JSON.stringify(pausedFlags)}`);
    return pausedFlags;
  }, {
    fix: "При паузе кнопки Сбор, Снимок, Обучить, Paper exam и Авто должны быть disabled и aria-disabled=\"true\" одновременно. Отказ сервера 409 paused проверяется отдельным POST, без клика по disabled.",
  });

  await step("auto_snapshot_after_growth", async () => {
    const before = await labeledValue(page, "Matured");
    await button(page, "Сбор").then((node) => node.click());
    await poll(async () => labeledValue(page, "Matured"), (value) => value === "400", 15000);
    await showTab(page, "Данные");
    const text = await poll(async () => cardText(page, "Снимки"), (value) => /synthds2/.test(value), 25000);
    screenshots.push(await shot(page, args.out, "browser-fixture-auto-snapshot-1440"));
    return { before, text: text.slice(0, 300) };
  }, { required: true });

  await step("keyboard_focus_and_stop_cancel", async () => {
    const before = await modeText(page);
    let outline = null;
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press("Tab");
      outline = await page.evaluate(() => {
        const el = document.activeElement;
        if (!el) return null;
        const style = getComputedStyle(el);
        return {
          tag: el.tagName.toLowerCase(),
          text: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80),
          outlineStyle: style.outlineStyle,
          outlineWidth: style.outlineWidth,
        };
      });
      if (outline && outline.outlineStyle !== "none" && outline.outlineWidth !== "0px") break;
    }
    if (!outline || outline.outlineStyle === "none") throw new Error(`no focus ring ${JSON.stringify(outline)}`);
    await button(page, "Стоп").then((node) => node.focus());
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ state: "visible" });
    const focused = await page.evaluate(() => document.activeElement && document.activeElement.id);
    if (focused !== "dialog-ok") throw new Error(`dialog focus ${focused}`);
    await page.keyboard.press("Tab");
    const next = await page.evaluate(() => document.activeElement && document.activeElement.id);
    if (next !== "dialog-cancel") throw new Error(`tab trap went to ${next}`);
    await page.keyboard.press("Tab");
    const wrapped = await page.evaluate(() => document.activeElement && document.activeElement.id);
    if (wrapped !== "dialog-ok") throw new Error(`tab wrap went to ${wrapped}`);
    screenshots.push(await shot(page, args.out, "browser-fixture-stop-dialog-1440"));
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });
    const after = await modeText(page);
    if (after !== before) throw new Error(`escape changed mode ${before} -> ${after}`);
    await button(page, "Стоп всё").then((node) => node.click());
    await dialog.waitFor({ state: "visible" });
    const warning = await dialog.innerText();
    if (!/prospective|доказательства|B09/.test(warning)) throw new Error(warning);
    await page.getByRole("button", { name: "Отмена", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    const still = await modeText(page);
    if (still !== before) throw new Error(`cancel changed mode ${before} -> ${still}`);
    screenshots.push(await shot(page, args.out, "browser-fixture-stop-cancelled-1440"));
    return { outline, before, after: still };
  }, { required: true });

  await step("stop_confirmed", async () => {
    await button(page, "Стоп").then((node) => node.click());
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
    const mode = await poll(async () => modeText(page), (value) => /stopped/.test(value), 10000);
    screenshots.push(await shot(page, args.out, "browser-fixture-stopped-1440"));
    return { mode };
  }, { required: true });

  await step("refresh_preserves_stopped_state", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    await poll(async () => modeText(page), (value) => /stopped/.test(value), 10000);
    await showTab(page, "Данные");
    const snapshots = await cardText(page, "Снимки");
    await showTab(page, "Модели");
    const models = await cardText(page, "Кандидаты и validation");
    if (!/synthds1/.test(snapshots) || !/synthcand1/.test(models)) {
      throw new Error(JSON.stringify({ snapshots: snapshots.slice(0, 200), models: models.slice(0, 200) }));
    }
    screenshots.push(await shot(page, args.out, "browser-fixture-refresh-1440"));
    return { mode: await modeText(page) };
  }, { required: true });

  await step("restart_preserves_journal", async () => {
    await stopFixture(child);
    child = null;
    await new Promise((resolve) => setTimeout(resolve, 400));
    const again = startFixture(info.port, info.root);
    child = again.child;
    const next = await again.ready;
    if (next.root !== info.root) throw new Error(`root changed ${next.root}`);
    await page.goto(next.url, { waitUntil: "domcontentloaded" });
    const mode = await poll(async () => modeText(page), (value) => /stopped/.test(value), 10000);
    await showTab(page, "Данные");
    const snapshots = await cardText(page, "Снимки");
    await showTab(page, "Модели");
    const models = await cardText(page, "Кандидаты и validation");
    if (!/synthds1/.test(snapshots) || !/synthds2/.test(snapshots) || !/synthcand1/.test(models)) {
      throw new Error(JSON.stringify({ snapshots: snapshots.slice(0, 240), models: models.slice(0, 240) }));
    }
    const leaked = await page.locator("body").innerText();
    if (leaked.includes("/Users/") || leaked.includes("operator.sqlite")) throw new Error("path leaked into UI");
    screenshots.push(await shot(page, args.out, "browser-fixture-restart-1440"));
    return { mode, url: next.url };
  }, { required: true });

  await auditWidths(page, args.out, "fixture-stopped", screenshots, layouts);
  await page.setViewportSize({ width: 390, height: 844 });
  await button(page, "Стоп").then((node) => node.click());
  screenshots.push(await shot(page, args.out, "browser-fixture-dialog-390"));
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 1000 });

  await step("layout_widths", async () => {
    const overflows = layouts.filter((layout) => layout.pageOverflow > 1);
    if (overflows.length) throw new Error(JSON.stringify(overflows));
    return layouts.map((layout) => ({
      stem: layout.stem,
      width: layout.width,
      pageOverflow: layout.pageOverflow,
      tables: layout.tables.length,
      contrast: layout.contrast,
    }));
  }, {
    fix: "Убрать горизонтальный overflow страницы. Широкие таблицы держать внутри .panel с overflow:auto.",
  });

  await step("contrast_labels", async () => {
    const sample = layouts.find((layout) => layout.contrast != null);
    if (!sample) return { measured: false };
    if (sample.contrast < 4.5) {
      throw new Error(`contrast ${sample.contrast.toFixed(2)} ${sample.labelColor} on ${sample.cardColor}`);
    }
    return { contrast: sample.contrast, label: sample.labelColor, card: sample.cardColor };
  }, {
    fix: "Поднять контраст подписей .k (--faint) относительно карточки как минимум до 4.5:1.",
  });

  await step("footer_port_and_deadline_text", async () => {
    const foot = await page.locator("footer").innerText();
    const port = new URL(page.url()).port;
    await showTab(page, "Обучение");
    const quota = await cardText(page, "Квоты");
    const problems = [];
    if (!foot.includes(port)) problems.push(`footer=${foot}`);
    if (/\d{16,}/.test(quota)) problems.push(`quota=${quota}`);
    if (problems.length) throw new Error(problems.join("\n").slice(0, 700));
    return { port, quota: quota.slice(0, 240) };
  }, {
    fix: "Подвал не должен всегда писать порт 8792: показывать фактический location.port. Поле «Не раньше» не должно печатать сырой ns; нужно местное время или «уже можно», если момент прошёл.",
  });

  await step("no_js_errors", async () => {
    const unexpected = jsErrors.filter((item) => {
      if (item.type !== "console") return true;
      return !/Failed to load resource: the server responded with a status of (403|409)/.test(item.text);
    });
    if (unexpected.length) throw new Error(JSON.stringify(unexpected));
    return {
      pageerrors: unexpected.length,
      expected_network_logs: jsErrors.filter((item) => item.type === "console"),
    };
  });

  const status = checks.some((item) => item.status === "fail" || item.status === "blocked") ? "fail" : "pass";
  return finish(args, label, {
    status,
    data_kind: "synthetic",
    proves_market_performance: false,
    proves_production_refit_interval: false,
    mutation_scope: "synthetic_fixture_only",
    fixture: info,
    checks,
    screenshots,
    layouts,
    js_errors: jsErrors,
    ui_fixes: fixes,
    paper_samples: paperSamples,
    ui_files: {
      web: stamp(path.join(REPO, "src/memecoin_operator/web.py")),
      runtime: stamp(path.join(REPO, "src/memecoin_operator/runtime.py")),
    },
  }, async () => {
    if (browser) await browser.close();
    await stopFixture(child);
  });
}

async function controlFlags(page) {
  const names = ["Сбор", "Снимок", "Обучить", "Paper exam", "Авто"];
  const rows = [];
  for (const name of names) {
    const node = await button(page, name);
    const state = await node.evaluate((el) => ({
      disabled: el.disabled === true,
      ariaDisabled: el.getAttribute("aria-disabled"),
    }));
    rows.push({ name, ...state });
  }
  return rows;
}

async function postCommand(page, action, params, requestId) {
  return page.evaluate(
    async ({ action, params, requestId }) => {
      const token = document.querySelector('meta[name="operator-token"]').content;
      const response = await fetch("/api/command", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Operator-Token": token,
        },
        body: JSON.stringify({ request_id: requestId, action, params }),
      });
      return { status: response.status, body: await response.json() };
    },
    { action, params, requestId },
  );
}

async function runReadonly(args) {
  const label = args.label || "readonly";
  const screenshots = [];
  const layouts = [];
  const checks = [];
  const jsErrors = [];
  const fixes = [];
  const gate = { ignore: false, allowNetwork: false };
  let browser = null;
  const record = (id, status, detail, fix) => {
    checks.push({ id, status, detail: detail ?? null, fix: fix || null });
    if (status === "fail" && fix) fixes.push({ id, fix });
  };
  fs.mkdirSync(args.out, { recursive: true });
  const launched = await launchBrowser();
  browser = launched.browser;
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    locale: "ru-RU",
    colorScheme: "dark",
  });
  page.setDefaultTimeout(12000);
  attachErrors(page, jsErrors, gate);
  let responseStatus = null;
  try {
    const response = await page.goto(args.baseUrl, { waitUntil: "domcontentloaded", timeout: 8000 });
    responseStatus = response ? response.status() : null;
  } catch (err) {
    record("page_open", "unreachable", String(err.message || err), null);
    await browser.close();
    return finish(args, label, {
      status: "unreachable",
      data_kind: "actual",
      base_url: args.baseUrl,
      checks,
      screenshots,
      layouts,
      js_errors: jsErrors,
      ui_fixes: fixes,
      proves_market_performance: false,
    });
  }
  record("page_open", responseStatus && responseStatus < 400 ? "pass" : "fail", { status: responseStatus });
  await page.waitForTimeout(1200);
  const title = await page.title();
  const found = await catalog(page);
  record("dom_catalog", "pass", { title, controls: found.slice(0, 80) });
  const body = await page.locator("body").innerText();
  const looksOperator = found.some((item) => item.name === "Сбор") || /Операторский центр/.test(body);
  if (looksOperator) {
    for (const name of ["Обзор", "Данные", "Paper", "Система"]) {
      const tab = page.getByRole("tab", { name, exact: true });
      if ((await tab.count()) > 0) await tab.first().click();
    }
    await page.getByRole("tab", { name: "Обзор", exact: true }).click();
  }
  screenshots.push(await shot(page, args.out, `browser-${label}-1440`));
  await auditWidths(page, args.out, label, screenshots, layouts);
  const overflows = layouts.filter((layout) => layout.pageOverflow > 1);
  record(
    "layout_widths",
    overflows.length ? "fail" : "pass",
    overflows.length ? overflows : layouts.map((layout) => ({ width: layout.width, pageOverflow: layout.pageOverflow })),
    overflows.length ? "Убрать горизонтальный overflow на этой ширине." : null,
  );
  record("no_js_errors", jsErrors.length ? "fail" : "pass", jsErrors);
  record("mutations", "pass", { posts: 0, note: "read-only pass did not send POST /api/command" });
  const status = checks.some((item) => item.status === "fail") ? "fail" : "observed";
  await browser.close();
  return finish(args, label, {
    status,
    data_kind: "actual",
    base_url: args.baseUrl,
    proves_market_performance: false,
    workflow: "not_run",
    checks,
    screenshots,
    layouts,
    js_errors: jsErrors,
    ui_fixes: fixes,
    title,
  });
}

async function finish(args, label, report, cleanup) {
  report.generated_at = new Date().toISOString();
  report.label = label;
  report.widths = WIDTHS.map((item) => item.width);
  const file = path.join(args.out, `browser-${label}-acceptance.json`);
  fs.writeFileSync(file, `${JSON.stringify(report, null, 2)}\n`);
  if (cleanup) await cleanup();
  process.stdout.write(`${file}\nstatus=${report.status} data_kind=${report.data_kind}\n`);
  return report.status === "fail" ? 1 : 0;
}

function setScene(root, scene) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [FIXTURE, "--set-scene", scene, "--root", root], {
      cwd: REPO,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => {
      out += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      err += chunk.toString();
    });
    child.on("exit", (code) => {
      if (code !== 0) reject(new Error(`set-scene ${scene} exited ${code}\n${err}\n${out}`));
      else resolve(out.trim());
    });
  });
}

async function readStatus(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/status", { cache: "no-store" });
    const body = await response.json();
    return { http: response.status, body };
  });
}

async function waitStatus(page, accept, timeoutMs = 12000) {
  const started = Date.now();
  let last = null;
  while (Date.now() - started < timeoutMs) {
    last = await readStatus(page);
    if (accept(last.body)) return last.body;
    await page.waitForTimeout(350);
  }
  throw new Error(`status timeout: ${JSON.stringify(last && last.body).slice(0, 700)}`);
}

async function gateControl(page, name) {
  const button = page.getByRole("button", { name, exact: true });
  if ((await button.count()) === 0) return { missing: true, disabled: false, reason: "" };
  return button.first().evaluate((el) => {
    const ids = (el.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    const described = ids
      .map((id) => {
        const node = document.getElementById(id);
        return node ? node.innerText : "";
      })
      .join(" ");
    return {
      missing: false,
      disabled: el.disabled === true,
      ariaDisabled: el.getAttribute("aria-disabled"),
      reason: [el.getAttribute("aria-label") || "", el.getAttribute("title") || "", described]
        .join(" ")
        .replace(/\s+/g, " ")
        .trim(),
    };
  });
}

async function runRound2(args) {
  const label = args.final ? "final" : "fixture-round2";
  const prefix = `browser-${label}`;
  const auditPaper = args.final ? "final-paper" : "fixture-round2-paper";
  const auditData = args.final ? "final-data" : "fixture-round2-data";
  const projectionNotes = [];
  const screenshots = [];
  const layouts = [];
  const checks = [];
  const jsErrors = [];
  const fixes = [];
  const gate = { ignore: false, allowNetwork: false };
  const gatedNames = ["Сбор", "Снимок", "Обучить", "Paper exam", "Авто"];
  const summed = "1999.15000000";
  let child = null;
  let browser = null;
  let commandPosts = 0;
  const record = (id, status, detail, fix) => {
    checks.push({ id, status, detail: detail ?? null, fix: fix || null });
    if (status === "fail" && fix) fixes.push({ id, fix });
  };
  async function step(id, fn, options = {}) {
    try {
      const detail = await fn();
      record(id, "pass", detail ?? null, null);
      return detail;
    } catch (err) {
      record(id, "fail", String(err && err.stack ? err.stack : err), options.fix || null);
      return null;
    }
  }

  fs.mkdirSync(args.out, { recursive: true });
  const started = startFixture(args.port, null, ["--scene", "rich"]);
  child = started.child;
  let info;
  try {
    info = await started.ready;
  } catch (err) {
    record("fixture_server", "fail", String(err), "Rich fixture must boot before the UI critique.");
    return finish(args, label, {
      status: "fail",
      data_kind: "synthetic",
      proves_market_performance: false,
      proves_production_refit_interval: false,
      checks,
      screenshots,
      layouts,
      js_errors: jsErrors,
      ui_fixes: fixes,
    });
  }
  record("fixture_server", "pass", info);
  const root = info.root;
  const launched = await launchBrowser();
  browser = launched.browser;
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: "ru-RU",
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  attachErrors(page, jsErrors, gate);
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/api/command")) commandPosts += 1;
  });

  try {
  await page.goto(info.url, { waitUntil: "domcontentloaded" });
  const status = await waitStatus(
    page,
    (body) => body && body.source && body.source.freshness_s === 20 && body.gates,
    15000,
  );

  await step("synthetic_page_label", async () => {
    await poll(async () => modeText(page), (text) => text.includes("idle"), 10000);
    const harness = page.locator("#fixture-kind");
    await harness.waitFor({ state: "visible" });
    const harnessText = await harness.innerText();
    const chip = page.locator("#synth");
    const chipHidden = (await chip.count()) === 0 || (await chip.evaluate((el) => el.classList.contains("hidden") || el.getAttribute("hidden") != null));
    const chipText = (await chip.count()) ? await chip.innerText() : "";
    if (!/SYNTHETIC/.test(harnessText) && !/SYNTHETIC/.test(chipText)) {
      throw new Error(`harness=${harnessText} chip=${chipText}`);
    }
    const flagged = status.synthetic === true || status.data_kind === "synthetic";
    if (flagged && (chipHidden || !/SYNTHETIC/.test(chipText))) {
      throw new Error(`status flag set but #synth hidden text=${JSON.stringify(chipText)}`);
    }
    if (!flagged) {
      projectionNotes.push({
        id: "synthetic_flag",
        detail: "public status has no synthetic/data_kind; product chip stays hidden until parent projects it",
      });
    }
    return { harness: harnessText, chip: chipText, flagged };
  }, {
    fix: "Чип #synth уже смотрит на status.synthetic или status.data_kind. Публичный status эти поля не отдаёт, поэтому фикстура остаётся без продуктовой пометки. Проекция должна отдать флаг, а страница — показать его. Не писать SYNTHETIC на живом операторе без флага.",
  });

  await step("rich_api_projection", async () => {
    const problems = [];
    const source = status.source || {};
    const coverage = status.coverage || {};
    const pools = Array.isArray(source.per_pool) ? source.per_pool : [];
    const span = Number(source.last_s) - Number(source.first_s);
    if (pools.length !== 24) problems.push(`per_pool ${pools.length}`);
    if (source.event_days !== 91) problems.push(`event_days ${source.event_days}`);
    if (!(span >= 91 * 86400)) problems.push(`span_s ${span}`);
    const lanes = pools.map((row) => row.lane || row.role || "");
    const count = (name) => lanes.filter((item) => item === name).length;
    if (count("hot") !== 6 || count("cold") !== 15 || count("quarantine") !== 3) {
      problems.push(`lanes hot=${count("hot")} cold=${count("cold")} quarantine=${count("quarantine")}`);
    }
    const quarantine = Array.isArray(source.quarantine) ? source.quarantine : [];
    if (quarantine.length !== 3) problems.push(`quarantine ${quarantine.length}`);
    if (!Array.isArray(source.hot_pools) || source.hot_pools.length !== 6) {
      projectionNotes.push({ id: "hot_pools", detail: "public status dropped source.hot_pools" });
    }
    const snap = (status.snapshots || []).find((item) => item.dataset_id === "synthdsR");
    const train = snap && snap.retained && snap.retained.train;
    if (!train || train.decisions !== 4800 || train.pools !== 18) problems.push("retained train missing");
    if (!snap || snap.historical_test_independent !== false) problems.push("historical_test_independent");
    const good = (status.candidates || []).find((item) => item.candidate_id === "synthcandA");
    if (!good || good.model_sha !== "a1".repeat(32) || !good.support || good.support.train.pools !== 18) {
      problems.push("candidate hash/support missing");
    }
    if (!good || !good.validation || good.validation.gru !== 0.25) problems.push("candidate validation missing");
    const failed = (status.candidates || []).find((item) => item.candidate_id === "synthcandF");
    if (!failed || failed.status !== "failed") problems.push(`failed status ${failed && failed.status}`);
    const incomplete = (status.candidates || []).find((item) => item.candidate_id === "synthcandI");
    if (!incomplete) problems.push("synthcandI missing");
    else if (incomplete.model_sha) problems.push("incomplete candidate has model_sha");
    else if (incomplete.status !== "incomplete") {
      projectionNotes.push({
        id: "incomplete_status",
        detail: `projected status ${incomplete.status}; UI must still keep it out of the paper select`,
      });
    }
    const paper = status.paper || {};
    if (paper.independent_books !== true) problems.push("independent_books");
    const books = Array.isArray(paper.books) ? paper.books : [];
    const bookA = books.find((item) => item.exam_id === "synthexamA");
    const bookB = books.find((item) => item.exam_id === "synthexamB");
    if (!bookA || bookA.realized !== "-1.25000000" || !bookB || bookB.realized !== "0.40000000") {
      problems.push("books missing separate realized");
    }
    if (String(paper.equity) !== summed) problems.push(`paper.equity ${paper.equity} expected adapter sum ${summed}`);
    const decisions = paper.decisions || [];
    const intent = decisions.find((item) => item.stage === "intent" && item.filled === false);
    const fill = decisions.find((item) => item.stage === "fill" && item.filled === true);
    if (!intent || !fill) problems.push("pending intent vs filled missing");
    const positions = paper.positions || [];
    if (!positions.some((item) => item.status === "pending") || !positions.some((item) => item.status === "open")) {
      problems.push("position pending/open missing");
    }
    if (status.gates.min_refit_interval_s !== 86400) problems.push(`interval ${status.gates.min_refit_interval_s}`);
    const raw = JSON.stringify(status);
    if (raw.includes("/Users/") || raw.includes("Traceback")) problems.push("public status leaks path or traceback");
    if (problems.length) throw new Error(problems.join("\n"));
    return {
      pools: pools.length,
      books: books.length,
      interval_s: status.gates.min_refit_interval_s,
      proves_production_refit_interval: false,
    };
  }, {
    fix: "Публичный status должен сохранить hot_pools, quarantine, retained, model_sha/support и не превращать status=incomplete в completed. Сумма cash/equity может прийти как агрегат адаптера, но не как единственный портфель.",
  });

  await step("markets_and_folds_visible", async () => {
    await showTab(page, "Данные");
    const markets = await cardText(page, "Рынки");
    const snaps = await cardText(page, "Снимки");
    const source = await cardText(page, "Источник и бюджет");
    const problems = [];
    if (!markets.includes("SYN01") || !markets.includes("SYN22")) problems.push("pool ids missing");
    if (!markets.includes("hot") || !markets.includes("cold") || !markets.includes("quarantine")) {
      problems.push(`classes missing\n${markets.slice(0, 400)}`);
    }
    if (!markets.includes("duplicate_bar_in_response")) problems.push("quarantine reason missing");
    if (!source.includes("91")) problems.push(`event days missing\n${source.slice(0, 300)}`);
    if (!snaps.includes("decisions 4800") || !snaps.includes("reused/descriptive")) {
      problems.push(`folds\n${snaps.slice(0, 400)}`);
    }
    screenshots.push(await shot(page, args.out, prefix + "-data-1440"));
    if (problems.length) throw new Error(problems.join("\n"));
    return { markets: markets.slice(0, 180) };
  }, {
    fix: "Таблица «Рынки» должна показать 24 пула, классы hot/cold/quarantine и причину quarantine. «Снимки» должны показать retained train/validation/test и флаг reused/descriptive. Если класс пустой, читать source.quarantine и source.hot_pools, не подставлять выдуманный класс.",
  });

  await step("candidate_support_and_failed_label", async () => {
    await showTab(page, "Модели");
    const models = await cardText(page, "Кандидаты и validation");
    const options = await page.locator("#cand option").allTextContents();
    const problems = [];
    if (!models.includes("a1a1a1a1a1a1")) problems.push("model sha prefix missing");
    if (!models.includes("gru 0.25")) problems.push("validation missing");
    if (!models.includes("pools 18") && !models.includes("decisions 4800")) problems.push("candidate support missing");
    if (models.includes("[object Object]")) problems.push("selected object rendered as [object Object]");
    if (!models.includes("gru")) problems.push("selected backbone missing");
    if (!models.includes("synthcandF")) problems.push("failed candidate hidden");
    if (!/failed|неуспех|сбой обучен/i.test(models)) problems.push("failed status not labeled");
    if (options.some((item) => item.includes("synthcandF"))) problems.push("failed candidate is selectable");
    if (options.some((item) => item.includes("synthcandI"))) {
      problems.push(`incomplete candidate is selectable: ${options.join(" | ")}`);
    }
    screenshots.push(await shot(page, args.out, prefix + "-models-1440"));
    if (problems.length) throw new Error(problems.join("\n"));
    return { options };
  }, {
    fix: "В реестре показать status failed отдельной подписью и не класть его в select paper. Кандидат без model_sha/support не предлагать для paper, даже если проекция сказала completed. selected рисовать как backbone/recipe, не String(object). Support fold показать рядом с hash.",
  });

  await step("book_selection_changes_pnl", async () => {
    await showTab(page, "Paper");
    const book = page.getByRole("combobox", { name: /Книга/ });
    if ((await book.count()) === 0) throw new Error("no book combobox");
    await book.first().selectOption("synthexamA");
    const equityA = await page.locator("#equity").innerText();
    const realizedA = await page.locator("#realized").innerText();
    await book.first().selectOption("synthexamB");
    const equityB = await page.locator("#equity").innerText();
    const realizedB = await page.locator("#realized").innerText();
    const books = await cardText(page, "Книги");
    const problems = [];
    if (equityA !== "998.75000000" || realizedA !== "-1.25000000") {
      problems.push(`book A equity=${equityA} realized=${realizedA}`);
    }
    if (equityB !== "1000.40000000" || realizedB !== "0.40000000") {
      problems.push(`book B equity=${equityB} realized=${realizedB}`);
    }
    if (equityA === summed || equityB === summed || books.includes(summed)) {
      problems.push("summed equity is shown as a portfolio figure");
    }
    screenshots.push(await shot(page, args.out, prefix + "-paper-1440"));
    if (problems.length) throw new Error(problems.join("\n"));
    return { equityA, equityB, realizedA, realizedB };
  }, {
    fix: "Выбор «Книга» меняет Equity/Realized на числа этой книги. Сумма независимых книг не показывается как один портфель.",
  });

  await step("pending_distinct_from_fill", async () => {
    await showTab(page, "Paper");
    const decisions = await cardText(page, "Решения WAIT / ENTER / HOLD / EXIT");
    const ledger = await cardText(page, "Позиции и сделки");
    const problems = [];
    if (!decisions.includes("intent") || !decisions.includes("не filled") || !decisions.includes("намерение, не сделка")) {
      problems.push(`intent row missing\n${decisions.slice(0, 500)}`);
    }
    if (!/\bfill\b/.test(decisions) || !decisions.includes("filled")) problems.push("fill row missing");
    if (!ledger.includes("pending") || !ledger.includes("open")) problems.push(`ledger\n${ledger.slice(0, 400)}`);
    if (problems.length) throw new Error(problems.join("\n"));
    return { decisions: decisions.slice(0, 240) };
  }, {
    fix: "ENTER intent помечать как намерение и не filled. Fill помечать filled. Pending-позицию не рисовать как уже открытую сделку.",
  });

  await step("layout_widths", async () => {
    await showTab(page, "Paper");
    await auditWidths(page, args.out, auditPaper, screenshots, layouts);
    await page.setViewportSize({ width: 390, height: 844 });
    await showTab(page, "Данные");
    const narrow = await measure(page);
    narrow.width = 390;
    narrow.stem = auditData;
    layouts.push(narrow);
    screenshots.push(await shot(page, args.out, prefix + "-data-390"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    const overflow = layouts.filter((item) => item.pageOverflow > 1);
    if (overflow.length) throw new Error(JSON.stringify(overflow));
    return layouts.map((item) => ({ stem: item.stem, width: item.width, pageOverflow: item.pageOverflow }));
  }, {
    fix: "На 1440/1100/390 страница не должна разъезжаться по горизонтали. Широкие таблицы держать внутри .panel.",
  });

  await step("table_headers_readable", async () => {
    const problems = [];
    for (const tab of ["Данные", "Paper", "Модели"]) {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await showTab(page, tab);
      const crushed = await page.evaluate(() => {
        const found = [];
        for (const cell of document.querySelectorAll("th")) {
          if (!cell.offsetParent) continue;
          const lines = cell.innerText.split("\n").map((line) => line.trim()).filter(Boolean);
          if (lines.length >= 3 && lines.every((line) => line.length === 1)) found.push(lines.join(""));
        }
        return found;
      });
      if (crushed.length) problems.push(`${tab}: ${crushed.join(", ")}`);
    }
    if (problems.length) throw new Error(problems.join("\n"));
    return { width: 1440 };
  }, {
    fix: "Заголовки таблиц не дробить по одной букве. Оставить nowrap и горизонтальный скролл внутри панели.",
  });

  await step("legacy_links_follow_projection", async () => {
    await showTab(page, "Система");
    const services = await page.locator("#services").innerText();
    const hrefs = await page.locator("a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") || ""));
    const headerHrefs = await page.locator("header a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href") || ""));
    const problems = [];
    if (!services.includes("synthactive") || !hrefs.some((href) => href.includes("18880"))) {
      problems.push("services missing synthactive:18880");
    }
    if (!services.includes("synthhist") || !hrefs.some((href) => href.includes("18881"))) {
      problems.push("services missing synthhist:18881");
    }
    if (!headerHrefs.some((href) => href.includes("18880"))) {
      problems.push(`header missed active legacy port: ${headerHrefs.join(" ")}`);
    }
    if (headerHrefs.some((href) => href.includes("18881"))) {
      problems.push("stopped legacy port is linked from the header");
    }
    if (problems.length) throw new Error(problems.join("\n"));
    return { headerHrefs };
  }, {
    fix: "Ссылки в шапке брать из status.legacy. Зашитые только 8790/8791 не заменяют порт и подпись проекции. В «Сервисах» тоже показывать эти записи.",
  });

  await step("host_display", async () => {
    const fresh = await readStatus(page);
    const host = fresh.body.host;
    await showTab(page, "Система");
    const text = await page.locator("#host").innerText();
    const raw = JSON.stringify(host || {});
    if (raw.includes("/Users/") || text.includes("/Users/") || text.includes("Traceback")) {
      throw new Error("host display leaks a path or traceback");
    }
    if (!host) {
      projectionNotes.push({ id: "host", detail: "status.host is null" });
      if (!/status\.host нет/.test(text)) throw new Error(text.slice(0, 300));
      return { absent: true };
    }
    if (!/Диск/.test(text) || !/Питание/.test(text)) throw new Error(text.slice(0, 400));
    screenshots.push(await shot(page, args.out, prefix + "-system-1440"));
    return { keys: Object.keys(host) };
  }, {
    fix: "На вкладке «Система» показать host snapshot: диск, питание или uptime. Пути диска и traceback не печатать.",
  });

  await step("paused_controls_and_api", async () => {
    await setScene(root, "rich");
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitStatus(page, (body) => body.domain_stop === false && body.expired === false);
    await button(page, "Пауза").then((node) => node.click());
    await poll(async () => modeText(page), (value) => /paused/.test(value), 10000);
    const flags = await controlFlags(page);
    const gate = await page.locator("#gate-line").innerText();
    const api = await postCommand(page, "collect", { profile: "balanced" }, "finalpause01");
    const problems = [];
    for (const row of flags) {
      if (row.disabled !== true || row.ariaDisabled !== "true") {
        problems.push(`${row.name} disabled=${row.disabled} aria-disabled=${row.ariaDisabled}`);
      }
    }
    if (!/Пауза|paused/.test(gate)) problems.push(`gate=${gate}`);
    if (api.status !== 409 || api.body.reason !== "paused") problems.push(JSON.stringify(api));
    await button(page, "Продолжить").then((node) => node.click());
    await poll(async () => modeText(page), (value) => /idle/.test(value), 10000);
    if (problems.length) throw new Error(problems.join("\n"));
    return { api: api.body.reason, flags };
  }, {
    fix: "На паузе Сбор, Снимок, Обучить, Paper exam и Авто disabled и aria-disabled=\"true\", причина про паузу в доступном описании. 409 paused приходит отдельным POST. Disabled-кнопку не кликать.",
  });

  await step("source_stale", async () => {
    await setScene(root, "stale");
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitStatus(page, (body) => body.source && body.source.freshness_s === 1200 && body.source.healthy === false);
    await poll(async () => page.locator("#heartbeat").innerText(), (text) => text === "нет", 10000);
    const fresh = await labeledValue(page, "Свежесть");
    const heart = await page.locator("#heartbeat").innerText();
    if (fresh !== "1200 с" || heart === "жив") throw new Error(`fresh=${fresh} heart=${heart}`);
    screenshots.push(await shot(page, args.out, prefix + "-stale-1440"));
    return { fresh, heart };
  }, {
    fix: "При freshness 1200 и healthy=false источник не должен читаться как живой. Число свежести остаётся видимым.",
  });

  await step("source_cooldown", async () => {
    await setScene(root, "cooldown");
    await page.reload({ waitUntil: "domcontentloaded" });
    const body = await waitStatus(
      page,
      (item) =>
        item.source &&
        Number(item.source.cooldown_until_ns) > Date.now() * 1e6 &&
        item.source.healthy === false,
    );
    await poll(async () => page.locator("#heartbeat").innerText(), (text) => text === "нет", 10000);
    const cell = await labeledValue(page, "Cooldown");
    if (!cell || cell === "нет" || cell === "нет метки" || /\d{16,}/.test(cell)) {
      throw new Error(`cooldown cell=${cell} api=${body.source.cooldown_until_ns}`);
    }
    screenshots.push(await shot(page, args.out, prefix + "-cooldown-1440"));
    return { cell };
  }, {
    fix: "Будущий cooldown показывать местным временем или «через …», не сырым ns и не словом «нет».",
  });

  async function expectGated(scene, pattern, shotName) {
    await setScene(root, scene);
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitStatus(page, (body) => (scene === "expired" ? body.expired === true : body.domain_stop === true));
    await poll(async () => page.locator("#gate-line").innerText(), (text) => pattern.test(text), 10000);
    const rows = [];
    const problems = [];
    for (const name of gatedNames) {
      const state = await gateControl(page, name);
      rows.push({ name, ...state });
      if (state.missing) problems.push(`${name} missing`);
      if (state.disabled !== true || state.ariaDisabled !== "true") {
        problems.push(`${name} disabled=${state.disabled} aria-disabled=${state.ariaDisabled}`);
      }
      if (!pattern.test(state.reason || "")) problems.push(`${name} reason=${state.reason}`);
    }
    const api = await postCommand(
      page,
      "collect",
      { profile: "balanced" },
      scene === "expired" ? "expiregate01" : "domaingate01",
    );
    const wanted = scene === "expired" ? "expired" : "domain_stop";
    if (api.status !== 409 || api.body.reason !== wanted) problems.push(`api ${JSON.stringify(api)}`);
    screenshots.push(await shot(page, args.out, shotName));
    if (problems.length) throw new Error(`${problems.join("\n")}\n${JSON.stringify(rows)}`);
    return rows;
  }

  await step("domain_stop_disables_controls", async () => {
    return expectGated("domain_stop", /Доменный STOP|domain_stop/, prefix + "-domain-stop-1440");
  }, {
    fix: "При domain_stop кнопки Сбор, Снимок, Обучить, Paper exam и Авто должны быть одновременно disabled и aria-disabled=\"true\". Описание — «Доменный STOP этой кнопкой не снимается». Отказ 409 проверяется POST, без клика.",
  });

  await step("expired_disables_controls", async () => {
    return expectGated("expired", /Срок конфигурации истёк|expired/, prefix + "-expired-1440");
  }, {
    fix: "При истёкшем сроке те же кнопки disabled и aria-disabled=\"true\". Описание — «Срок конфигурации истёк». Отказ 409 проверяется POST, без клика.",
  });

  await step("recovery_error_readable", async () => {
    await setScene(root, "rich");
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitStatus(page, (body) => body.expired === false && body.domain_stop === false && body.source.healthy === true);
    const collect = page.getByRole("button", { name: "Сбор", exact: true });
    if (await collect.isDisabled()) throw new Error("collect is disabled on the rich scene");
    await collect.click({ timeout: 3000 });
    await showTab(page, "Обучение");
    const jobs = await poll(
      async () => cardText(page, "Задачи"),
      (text) => /Прервано и не будет молча повторено/.test(text),
      20000,
    );
    const pageText = await page.locator("body").innerText();
    if (pageText.includes("Traceback") || pageText.includes("/Users/")) {
      throw new Error(pageText.slice(0, 500));
    }
    screenshots.push(await shot(page, args.out, prefix + "-recovery-1440"));
    return { jobs: jobs.slice(0, 300) };
  }, {
    fix: "Сбой сбора с причиной interrupted показывать русской фразой в задачах. Traceback и путь файловой системы на страницу не выводить.",
  });

  await step("keyboard_dialog_focus", async () => {
    const before = await modeText(page);
    await page.locator("#act-stop").focus();
    await page.keyboard.press("Enter");
    await page.locator("#dialog").waitFor({ state: "visible" });
    const first = await page.evaluate(() => document.activeElement && document.activeElement.id);
    await page.keyboard.press("Tab");
    const second = await page.evaluate(() => document.activeElement && document.activeElement.id);
    await page.keyboard.press("Tab");
    const third = await page.evaluate(() => document.activeElement && document.activeElement.id);
    await page.keyboard.press("Escape");
    await page.locator("#dialog").waitFor({ state: "hidden" });
    const after = await modeText(page);
    screenshots.push(await shot(page, args.out, prefix + "-dialog-1440"));
    if (first !== "dialog-ok" || second !== "dialog-cancel" || third !== "dialog-ok") {
      throw new Error(`focus ${first} -> ${second} -> ${third}`);
    }
    if (after !== before) throw new Error(`mode ${before} -> ${after}`);
    return { first, second, third, mode: after };
  }, {
    fix: "Диалог стопа открывается с клавиатуры, фокус начинается на «Подтвердить», Tab ходит между «Отмена» и «Подтвердить», Escape отменяет без смены режима.",
  });

  await step("reduced_motion", async () => {
    const motion = await page.evaluate(() => {
      const css = [...document.styleSheets]
        .map((sheet) => {
          try {
            return [...sheet.cssRules].map((rule) => rule.cssText).join("\n");
          } catch (err) {
            return "";
          }
        })
        .join("\n");
      const running = document.getAnimations().filter((item) => item.playState === "running");
      return {
        reduced: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
        declared: css.includes("prefers-reduced-motion"),
        running: running.length,
      };
    });
    if (!motion.reduced || !motion.declared || motion.running) throw new Error(JSON.stringify(motion));
    return motion;
  }, {
    fix: "В stylesheet оставить @media (prefers-reduced-motion: reduce) с отключением animation/transition. При reduce на странице не должно быть running-анимаций.",
  });

  await step("chart_axes_contrast_footer", async () => {
    await showTab(page, "Обзор");
    const sample = await measure(page);
    const foot = await page.locator("footer").innerText();
    const port = new URL(page.url()).port;
    screenshots.push(await shot(page, args.out, prefix + "-overview-1440"));
    await showTab(page, "Обучение");
    const quota = await cardText(page, "Квоты");
    const problems = [];
    if (!sample.chart || sample.chart.texts < 2) problems.push(`chart ${JSON.stringify(sample.chart)}`);
    if (sample.contrast != null && sample.contrast < 4.5) {
      problems.push(`contrast ${sample.contrast.toFixed(2)} ${sample.labelColor} on ${sample.cardColor}`);
    }
    if (!foot.includes(port)) problems.push(`footer=${foot}`);
    if (/\d{16,}/.test(quota)) problems.push(`raw ns in quota=${quota}`);
    if (problems.length) throw new Error(problems.join("\n"));
    return { contrast: sample.contrast, texts: sample.chart && sample.chart.texts, port };
  }, {
    fix: "У графика оставить читаемые подписи осей. Контраст подписей .k к карточке не ниже 4.5. Подвал показывает фактический порт. «Не раньше» не печатает сырой ns.",
  });

  await step("no_js_errors", async () => {
    const unexpected = jsErrors.filter((item) => {
      if (item.type !== "console") return true;
      return !/Failed to load resource: the server responded with a status of (403|409)/.test(item.text);
    });
    if (unexpected.length) throw new Error(JSON.stringify(unexpected));
    return { expected_network_logs: jsErrors.filter((item) => item.type === "console") };
  });

  } catch (err) {
    record(
      "round2_aborted",
      "fail",
      String(err && err.stack ? err.stack : err),
      "Прогон оборвался до отчёта. Это ошибка harness или селектора, не пропуск UI.",
    );
  }

  const failed = checks.some((item) => item.status === "fail");
  return finish(
    args,
    label,
    {
      status: failed ? "fail" : "pass",
      data_kind: "synthetic",
      proves_market_performance: false,
      proves_production_refit_interval: false,
      mutation_scope: "synthetic_fixture_only",
      fixture_min_refit_interval_s: info.fixture_min_refit_interval_s,
      production_min_refit_interval_s: 86400,
      note: "Config interval is 86400. This run does not wait 24h and does not prove the production gate.",
      projection_pending: projectionNotes,
      fixture: info,
      checks,
      screenshots,
      layouts,
      js_errors: jsErrors,
      ui_fixes: fixes,
      ui_files: {
        web: stamp(path.join(REPO, "src/memecoin_operator/web.py")),
        runtime: stamp(path.join(REPO, "src/memecoin_operator/runtime.py")),
        control: stamp(path.join(REPO, "src/memecoin_operator/control.py")),
      },
    },
    async () => {
      if (browser) await browser.close();
      await stopFixture(child);
    },
  );
}

async function main() {
  const args = parseArgs(process.argv);
  const code = args.final || args.round === 2
    ? await runRound2(args)
    : args.fixture
      ? await runFixture(args)
      : await runReadonly(args);
  process.exitCode = code;
}

main().catch(async (err) => {
  if (activeFixture && activeFixture.exitCode == null) activeFixture.kill("SIGTERM");
  process.stderr.write(`${err.stack || err}\n`);
  process.exitCode = 1;
});
