import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability, Snapshot } from "../../api";
import { ResearchPage } from "./ResearchPage";

type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "research-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function getBaseResearchData(): ResearchData {
  const pane = snapshotResponse.data.research;
  if (pane.availability !== "AVAILABLE") {
    throw new Error("published fixture must expose AVAILABLE research pane");
  }
  return structuredClone(pane.data);
}

function renderResearchWithSnapshot(
  response: typeof snapshotResponse = snapshotResponse,
) {
  const capabilities = response.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/research",
        element: <Outlet context={{ snapshotResponse: response, capabilities }} />,
        children: [{ index: true, element: <ResearchPage /> }],
      },
    ],
    { initialEntries: ["/research"] },
  );
  return render(<RouterProvider router={router} />);
}

function renderResearch() {
  return renderResearchWithSnapshot();
}

function renderSyntheticResearch(data: ResearchData) {
  const pane = snapshotResponse.data.research;
  if (pane.availability !== "AVAILABLE") {
    throw new Error("published fixture must expose AVAILABLE research pane");
  }
  return renderResearchWithSnapshot({
    ...snapshotResponse,
    snapshotId: "synthetic-research-adversarial",
    data: {
      ...snapshotResponse.data,
      research: { ...pane, data },
    },
  });
}

function assertNoPermissionReadinessLanguage() {
  const body = document.body.textContent ?? "";
  expect(body.toLowerCase()).not.toMatch(/permission granted/);
  expect(body).not.toMatch(/разрешено/i);
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(body).not.toMatch(/%/);
  expect(body).not.toMatch(/готовност|percent ready|% ready|readiness/i);
  expect(body).not.toMatch(/0\s+exposures|exposures:\s*0/i);
}

const reviewCodes = [
  "prospective_sampling_binding",
  "point_in_time_universe_scope",
  "conditional_target_semantics",
  "dependence_and_precision_plan",
  "runner_conformance",
  "exposure_ledger",
  "formal_forecast_admission",
];

describe("ResearchPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders independent stages and keeps 0/114 apart from engineering fits", () => {
    renderResearch();

    expect(screen.getByRole("heading", { name: "Независимые разрешения" })).toBeInTheDocument();
    expect(screen.getByLabelText("Study id")).toHaveTextContent("F01-v1");

    const recorded = screen.getByRole("article", { name: "Записанные research fits" });
    const engineering = screen.getByRole("article", { name: "Engineering fits" });
    expect(within(recorded).getByText("0/114")).toBeInTheDocument();
    expect(recorded).not.toHaveTextContent("135");
    expect(within(engineering).getByText("135")).toBeInTheDocument();
    expect(engineering).not.toHaveTextContent("0/114");
    expect(screen.getAllByText("0/114")).toHaveLength(1);
    expect(screen.getAllByText("135")).toHaveLength(1);

    const numeric = screen.getByRole("article", { name: "Числовые проверки" });
    expect(within(numeric).getByText("Есть блокеры")).toBeInTheDocument();
    expect(within(numeric).getByText("113")).toBeInTheDocument();
    expect(within(numeric).getByText("111")).toBeInTheDocument();
    expect(numeric).toHaveTextContent(/не открывает допуск/i);
    expect(screen.getByRole("region", { name: "Обязательные review" })).not.toHaveTextContent("111");

    expect(within(screen.getByRole("article", { name: "Frozen spec" })).getByText("Зафиксирован")).toBeInTheDocument();
    expect(
      within(screen.getByRole("article", { name: "Квалификация корпуса" })).getByText("Не квалифицирован"),
    ).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Допуск" })).getByText("LOCKED")).toBeInTheDocument();
    const runner = screen.getByRole("article", { name: "Runner" });
    expect(within(runner).getByText("Неизвестно")).toBeInTheDocument();
    expect(runner).not.toHaveTextContent("READY");
    expect(runner).not.toHaveTextContent("ENGINEERING");
    const holdout = screen.getByRole("article", { name: "Holdout" });
    expect(within(holdout).getByText("0")).toBeInTheDocument();
    expect(holdout).toHaveTextContent("PROTECTED");
    expect(holdout).toHaveTextContent("NOT_CERTIFIED");
    expect(holdout).toHaveTextContent(/не являются exposures/);
    expect(holdout).toHaveTextContent(/globally unseen/);

    expect(screen.getAllByText("REAL EVIDENCE")).toHaveLength(1);
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("DEMO — вымышленные данные")).not.toBeInTheDocument();
    expect(screen.getByText("Привязка prospective sampling")).toBeInTheDocument();
    expect(screen.getByText("Журнал exposures")).toBeInTheDocument();
    for (const code of reviewCodes) {
      expect(screen.queryByText(code)).not.toBeInTheDocument();
    }
    expect(screen.queryByText("FROZEN_DESIGN_NOT_ADMISSION")).not.toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toContain("1791331200000000000");
    expect(document.querySelector("[data-cc-scroll='research-codes-sampling']")).toBeNull();

    expect(screen.getByRole("link", { name: "Открыть календарь" })).toHaveAttribute("href", "/calendar");
    expect(screen.getByText(/CC-P001 — безопасный operational metadata export/)).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toContain("135/114");
    expect(document.body.textContent ?? "").not.toContain("249");
    assertNoPermissionReadinessLanguage();
  });

  it("mounts raw review codes only while the disclosure is open", () => {
    renderResearch();
    const button = screen.getByRole("button", { name: "Сырые коды, Выборка и границы" });
    button.focus();
    expect(button).toHaveFocus();

    fireEvent.click(button);
    expect(screen.getByText("prospective_sampling_binding")).toBeInTheDocument();
    expect(screen.getByText("point_in_time_universe_scope")).toBeInTheDocument();
    expect(screen.queryByText("runner_conformance")).not.toBeInTheDocument();
    const scroll = document.querySelector("[data-cc-scroll='research-codes-sampling']");
    expect(scroll).toBeTruthy();
    expect(scroll?.textContent).toContain("prospective_sampling_binding");

    fireEvent.click(button);
    expect(screen.queryByText("prospective_sampling_binding")).not.toBeInTheDocument();
    expect(document.querySelector("[data-cc-scroll='research-codes-sampling']")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Границы плана" }));
    expect(document.body.textContent ?? "").toContain("1791331200000000000");
    expect(document.querySelector("[data-cc-scroll='research-plan-bounds']")).toBeTruthy();
  });
});

describe("ResearchPage adversarial synthetic fixtures", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not treat numeric pass or a recorded launch flag as admission", () => {
    const base = getBaseResearchData();
    renderSyntheticResearch({
      ...base,
      numericBlockers: 0,
      numericChecks: 42,
      recordedProtocolFrozen: true,
      recordedResearchLaunchAuthorized: true,
      sampling: {
        ...base.sampling,
        recordedCollectionAuthorized: true,
        admittedWindows: 3,
      },
    });

    const numeric = screen.getByRole("article", { name: "Числовые проверки" });
    expect(within(numeric).getByText("Числовой pass")).toBeInTheDocument();
    expect(within(numeric).getByText("42")).toBeInTheDocument();
    expect(within(numeric).getByText("0")).toBeInTheDocument();
    expect(numeric).toHaveTextContent(/не открывает допуск/i);

    const admission = screen.getByRole("article", { name: "Допуск" });
    expect(within(admission).getByText("LOCKED")).toBeInTheDocument();
    expect(admission).toHaveTextContent("Записанный запуск: да");
    expect(admission).toHaveTextContent(/не открывает обучение/);
    expect(within(screen.getByRole("article", { name: "Квалификация корпуса" })).getByText("Записанный факт")).toBeInTheDocument();
    const runner = screen.getByRole("article", { name: "Runner" });
    expect(within(runner).getByText("Неизвестно")).toBeInTheDocument();
    expect(runner).not.toHaveTextContent("READY");
    expect(runner).not.toHaveTextContent("ENGINEERING");
    assertNoPermissionReadinessLanguage();
  });

  it("shows holdout releases without an exposure count", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      recordedHoldoutReleases: 0,
    });

    const holdout = screen.getByRole("article", { name: "Holdout" });
    expect(within(holdout).getByText("0")).toBeInTheDocument();
    expect(holdout).toHaveTextContent(/не являются exposures/);
    expect(holdout).toHaveTextContent(/не доказывает globally unseen/);
    expect(holdout).toHaveTextContent("NOT_CERTIFIED");
    expect(holdout).toHaveTextContent("PROTECTED");
  });

  it("keeps engineering and recorded fits separate when the values match", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      engineeringFits: 88,
      recordedResearchFits: 88,
      fitBudget: 120,
    });

    const recorded = screen.getByRole("article", { name: "Записанные research fits" });
    const engineering = screen.getByRole("article", { name: "Engineering fits" });
    expect(within(recorded).getByText("88/120")).toBeInTheDocument();
    expect(within(engineering).getByText("88")).toBeInTheDocument();
    expect(engineering).not.toHaveTextContent("88/120");
    expect(recorded).not.toHaveTextContent(/^88$/);
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("176");
    expect(body).not.toContain("88/88");
    assertNoPermissionReadinessLanguage();
  });

  it("renders unknown specId verbatim without GRU substitution", () => {
    const specId = "opaque-spec-UNKNOWN-7";
    renderSyntheticResearch({
      ...getBaseResearchData(),
      specId,
    });

    expect(screen.getByLabelText("Study id")).toHaveTextContent(specId);
    expect(document.body.textContent ?? "").not.toMatch(/\bGRU\b/i);
  });

  it("does not leak cast-only payloads or a hidden holdout report", () => {
    const poisoned = {
      ...getBaseResearchData(),
      label: "ADVERSARIAL_LABEL_PAYLOAD",
      target: "ADVERSARIAL_TARGET_PAYLOAD",
      holdoutMetric: "ADVERSARIAL_HOLDOUT_METRIC",
      holdoutReport: "ADVERSARIAL_HOLDOUT_REPORT",
      exposures: "ADVERSARIAL_EXPOSURES",
    } as ResearchData;

    renderSyntheticResearch(poisoned);

    const body = document.body.textContent ?? "";
    expect(body).not.toContain("ADVERSARIAL_LABEL_PAYLOAD");
    expect(body).not.toContain("ADVERSARIAL_TARGET_PAYLOAD");
    expect(body).not.toContain("ADVERSARIAL_HOLDOUT_METRIC");
    expect(body).not.toContain("ADVERSARIAL_HOLDOUT_REPORT");
    expect(body).not.toContain("ADVERSARIAL_EXPOSURES");
  });

  it("keeps an unknown review code behind its own disclosure", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      missingReviews: ["prospective_sampling_binding", "totally_unknown_review_code"],
    });

    expect(screen.getByText("Привязка prospective sampling")).toBeInTheDocument();
    expect(screen.getByText("Подробности недоступны")).toBeInTheDocument();
    expect(screen.queryByText("totally_unknown_review_code")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сырые коды, Прочие коды" }));
    expect(screen.getByText("totally_unknown_review_code")).toBeInTheDocument();
    expect(document.querySelector("[data-cc-scroll='research-codes-other']")).toBeTruthy();
  });

  it("maps DEMO provenance to the demo badge, not synthetic input", () => {
    const pane = snapshotResponse.data.research;
    if (pane.availability !== "AVAILABLE") {
      throw new Error("published fixture must expose AVAILABLE research pane");
    }
    renderResearchWithSnapshot({
      ...snapshotResponse,
      data: {
        ...snapshotResponse.data,
        research: { ...pane, provenance: "DEMO" },
      },
    });

    expect(screen.getAllByText("DEMO — вымышленные данные").length).toBeGreaterThan(0);
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
  });

  it("shows an unavailable research pane without fit counters", () => {
    renderResearchWithSnapshot({
      ...snapshotResponse,
      data: {
        ...snapshotResponse.data,
        research: {
          availability: "UNAVAILABLE",
          provenance: null,
          qualification: null,
          sourceIds: [],
          data: null,
          reasonCode: "RESEARCH_TEST_UNAVAILABLE",
        },
      },
    });

    expect(screen.getByText("RESEARCH_TEST_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText("0/114")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
