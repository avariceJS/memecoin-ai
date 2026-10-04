import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
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
  expect(body).not.toMatch(/%\s*ready|percent ready|готовность.*%/i);
}

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

  it("renders spec, gates, counters without permission or percent readiness", () => {
    renderResearch();

    expect(screen.getByRole("heading", { name: /Исследование \(recorded\)/i })).toBeInTheDocument();
    expect(screen.getByText("F01-v1")).toBeInTheDocument();
    expect(screen.getByText("0/114")).toBeInTheDocument();
    expect(screen.getAllByText("135").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("113").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("111").length).toBeGreaterThanOrEqual(1);

    expect(screen.getAllByText("REAL EVIDENCE").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("DEMO — вымышленные данные")).not.toBeInTheDocument();
    expect(screen.getAllByText("LOCKED").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("PROTECTED")).toBeInTheDocument();
    expect(screen.getByText("NOT_CERTIFIED")).toBeInTheDocument();

    expect(screen.getByText(/не global exposures/)).toBeInTheDocument();
    expect(screen.getByText(/не proof globally unseen/)).toBeInTheDocument();
    expect(screen.getByText(/prospective_sampling_binding/)).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /Открыть календарь/i })).toHaveAttribute(
      "href",
      "/calendar",
    );
    expect(screen.getByText(/CC-P001 — безопасный operational metadata export/)).toBeInTheDocument();

    const body = document.body.textContent ?? "";
    expect(body.toLowerCase()).not.toMatch(/permission granted/);
    expect(body).not.toMatch(/разрешено/i);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(body).not.toMatch(/%\s*ready|percent ready|готовность.*%/i);
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

  it("keeps LOCKED when blockers are 0 but checks are positive", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      numericBlockers: 0,
      numericChecks: 42,
    });

    expect(screen.getAllByText("LOCKED").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Numeric checks (recorded)").closest("article")).toHaveTextContent("42");
    expect(screen.getByText("Numeric blockers (recorded)").closest("article")).toHaveTextContent("0");
    assertNoPermissionReadinessLanguage();
  });

  it("shows holdout releases 0 with non-exposure / non-proof disclaimers", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      recordedHoldoutReleases: 0,
    });

    const protocolSection = screen.getByLabelText("Study id").closest("section");
    expect(protocolSection?.textContent).toMatch(/не global exposures/);
    expect(protocolSection?.textContent).toMatch(/не proof globally unseen/);
    expect(
      screen.getByText("Recorded holdout releases", { exact: false }).closest("div")?.querySelector("dd"),
    ).toHaveTextContent("0");
  });

  it("keeps engineering and recorded fits as separate fields when values match", () => {
    renderSyntheticResearch({
      ...getBaseResearchData(),
      engineeringFits: 88,
      recordedResearchFits: 88,
      fitBudget: 120,
    });

    expect(screen.getByText("Recorded research fits / fit budget").closest("article")).toHaveTextContent(
      "88/120",
    );
    expect(screen.getByText("Engineering fits (отдельно)").closest("article")).toHaveTextContent("88");
    expect(screen.getByText("Recorded research fits", { selector: "dt" })).toBeInTheDocument();
    expect(screen.getByText("Engineering fits", { selector: "dt" })).toBeInTheDocument();
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

  it("does not leak cast-only label/target/holdoutMetric keys into DOM", () => {
    const poisoned = {
      ...getBaseResearchData(),
      label: "ADVERSARIAL_LABEL_PAYLOAD",
      target: "ADVERSARIAL_TARGET_PAYLOAD",
      holdoutMetric: "ADVERSARIAL_HOLDOUT_METRIC",
    } as ResearchData;

    renderSyntheticResearch(poisoned);

    const body = document.body.textContent ?? "";
    expect(body).not.toContain("ADVERSARIAL_LABEL_PAYLOAD");
    expect(body).not.toContain("ADVERSARIAL_TARGET_PAYLOAD");
    expect(body).not.toContain("ADVERSARIAL_HOLDOUT_METRIC");
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

    expect(screen.getByText("DEMO — вымышленные данные")).toBeInTheDocument();
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
  });
});
