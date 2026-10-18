import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability, SnapshotResponse } from "../../api";
import { BenchmarkTimingsTable } from "./BenchmarkTimingsTable";
import { formatTimingMs } from "./format";
import { ModelsPage } from "./ModelsPage";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

function loadSnapshotResponse(): SnapshotResponse {
  return {
    apiVersion: "1.0",
    snapshotId: "models-fixture-snapshot",
    servedAt: "2026-10-03T12:00:00.000Z",
    data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
  };
}

function renderModels(snapshotResponse = loadSnapshotResponse()) {
  const capabilities = snapshotResponse.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/models",
        element: <Outlet context={{ snapshotResponse, capabilities }} />,
        children: [{ index: true, element: <ModelsPage /> }],
      },
    ],
    { initialEntries: ["/models"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("ModelsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders 15 runs and 40 benchmarks from published JSON without GRU-only title or winner", () => {
    const snapshotResponse = loadSnapshotResponse();
    const pane = snapshotResponse.data.models;
    if (pane.availability !== "AVAILABLE") throw new Error("expected models pane");
    expect(pane.data.runs).toHaveLength(15);
    expect(pane.data.benchmarks).toHaveLength(40);
    expect(pane.data.activeModel).toBeNull();

    renderModels(snapshotResponse);

    expect(
      screen.getByRole("heading", { name: /Models \/ Engineering evidence/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^GRU/i })).not.toBeInTheDocument();
    expect(screen.getByText("не выбрана")).toBeInTheDocument();
    expect(screen.getAllByText("ENGINEERING").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("SYNTHETIC INPUT").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("REAL dataset").length).toBe(5);

    const runsHeading = screen.getByRole("heading", { name: /Engineering smoke runs · 15/i });
    expect(runsHeading).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Benchmark timings · 40\/40/i })).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: /winner|promote|launch|resume/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/^cash model$/i)).not.toBeInTheDocument();

    expect(screen.getAllByRole("row").length).toBeGreaterThan(20);
  });

  it("shows несопоставимо for timing comparison when shape filter is all", () => {
    renderModels();
    expect(screen.getByText(/несопоставимо — сузьте device/i)).toBeInTheDocument();
    const compareCells = screen.getAllByText("несопоставимо");
    expect(compareCells.length).toBeGreaterThanOrEqual(40);
  });

  it("allows keyboard focus on table rows", () => {
    renderModels();
    const rows = screen.getAllByRole("row").filter((r) => r.getAttribute("tabindex") === "0");
    expect(rows.length).toBeGreaterThan(0);
  });
});

describe("formatTimingMs", () => {
  it("shows не измерено for null", () => {
    expect(formatTimingMs(null)).toBe("не измерено");
  });
});

describe("BenchmarkTimingsTable null fixture", () => {
  it("renders не измерено for null median timings", () => {
    const snapshotResponse = loadSnapshotResponse();
    const pane = snapshotResponse.data.models;
    if (pane.availability !== "AVAILABLE") throw new Error("expected models");
    const row = { ...pane.data.benchmarks[0]!, medianInferenceMs: null, medianForwardBackwardMs: null };

    render(<BenchmarkTimingsTable benchmarks={[row]} />);

    expect(screen.getAllByText("не измерено").length).toBeGreaterThanOrEqual(2);
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("не измерено").length).toBeGreaterThanOrEqual(2);
  });

  it("shows comparable min label when device and shape narrowed", () => {
    const snapshotResponse = loadSnapshotResponse();
    const pane = snapshotResponse.data.models;
    if (pane.availability !== "AVAILABLE") throw new Error("expected models");

    render(<BenchmarkTimingsTable benchmarks={pane.data.benchmarks} />);

    fireEvent.change(screen.getByLabelText(/Device/i), { target: { value: "cpu" } });
    fireEvent.change(screen.getByLabelText(/Shape/i), { target: { value: "16×8" } });

    expect(screen.queryByText(/несопоставимо — сузьте/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/мин\. в группе|\+.*% к min inf\./).length).toBeGreaterThan(0);
  });
});
