import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import type { Capability, SnapshotResponse } from "../../api";
import { parseSnapshot } from "../../api/parse";
import { BenchmarkTimingsTable } from "./BenchmarkTimingsTable";
import { LatencyComparison } from "./LatencyComparison";
import {
  groupMinimum,
  rowsForAxis,
  timingsComparable,
  type BenchmarkRow,
  type TimingMetric,
} from "./benchmarkFilters";
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

function availableModels(snapshotResponse = loadSnapshotResponse()) {
  const pane = snapshotResponse.data.models;
  if (pane.availability !== "AVAILABLE") throw new Error("expected models pane");
  return pane;
}

function region(name: "model-summary" | "benchmark-comparison"): HTMLElement {
  const element = document.querySelector(`[data-cc-region="${name}"]`);
  if (!(element instanceof HTMLElement)) throw new Error(`missing region ${name}`);
  return element;
}

function bench(id: string, overrides: Partial<BenchmarkRow> = {}): BenchmarkRow {
  return {
    id,
    modelKind: "linear",
    width: 16,
    context: 8,
    device: "cpu",
    batchSize: 1,
    parameters: 180,
    status: "ok",
    medianInferenceMs: 1,
    medianForwardBackwardMs: 2,
    scope: "ENGINEERING_BENCHMARK",
    inputKind: "SYNTHETIC",
    ...overrides,
  };
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

  it("renders architecture summary and matched charts without a market winner", () => {
    const pane = availableModels();
    expect(pane.data.runs).toHaveLength(15);
    expect(pane.data.benchmarks).toHaveLength(40);
    expect(pane.data.activeModel).toBeNull();

    renderModels();

    expect(screen.getByRole("heading", { name: "Сводка архитектур" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Сопоставимые замеры" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^GRU/i })).not.toBeInTheDocument();
    expect(screen.getByText("не выбрана")).toBeInTheDocument();
    expect(screen.getAllByText("нет").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /winner|promote|launch|resume/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/^cash model$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/победитель|мин\. в группе|\+\d+%/)).not.toBeInTheDocument();

    const summary = region("model-summary");
    expect(within(summary).getByText("15")).toBeInTheDocument();
    expect(within(summary).getByText("40")).toBeInTheDocument();
    expect(screen.getAllByText("REAL dataset")).toHaveLength(5);
    expect(document.querySelector("[data-cc-scroll='model-run-ids']")).toBeTruthy();

    const comparison = region("benchmark-comparison");
    expect(within(comparison).getByText("ENGINEERING")).toBeInTheDocument();
    expect(within(comparison).getByText("SYNTHETIC INPUT")).toBeInTheDocument();
    expect(within(comparison).getByRole("list", { name: "CPU и MPS" }).querySelectorAll("li")).toHaveLength(2);
    expect(within(comparison).getByRole("list", { name: "Архитектуры" }).querySelectorAll("li")).toHaveLength(5);
    expect(document.querySelector("[data-cc-scroll='benchmark-case-ids']")).toBeTruthy();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps synthetic input and engineering on the chart, and demo separate", () => {
    const snapshotResponse = loadSnapshotResponse();
    const pane = availableModels(snapshotResponse);
    renderModels({
      ...snapshotResponse,
      data: {
        ...snapshotResponse.data,
        models: { ...pane, provenance: "DEMO" },
      },
    });

    const comparison = region("benchmark-comparison");
    expect(within(comparison).getByText("DEMO — вымышленные данные")).toBeInTheDocument();
    expect(within(comparison).queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(within(comparison).queryByText("ENGINEERING")).not.toBeInTheDocument();
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
  });

  it("filters the matched charts from the keyboard and copies a run id", async () => {
    const pane = availableModels();
    const batchOne = pane.data.benchmarks.find(
      (row) =>
        row.modelKind === "gru" &&
        row.device === "cpu" &&
        row.batchSize === 1 &&
        row.width === 16 &&
        row.context === 8,
    );
    const batchMany = pane.data.benchmarks.find(
      (row) =>
        row.modelKind === "gru" &&
        row.device === "cpu" &&
        row.batchSize === 32 &&
        row.width === 16 &&
        row.context === 8,
    );
    if (!batchOne || !batchMany) throw new Error("expected gru cpu rows");

    renderModels();
    const batch = screen.getByLabelText("Пакет");
    batch.focus();
    expect(batch).toHaveFocus();
    const deviceChart = screen.getByRole("list", { name: "CPU и MPS" });
    expect(within(deviceChart).getByText(formatTimingMs(batchOne.medianInferenceMs))).toBeInTheDocument();

    fireEvent.change(batch, { target: { value: "32" } });
    const updatedChart = screen.getByRole("list", { name: "CPU и MPS" });
    expect(within(updatedChart).queryByText(formatTimingMs(batchOne.medianInferenceMs))).not.toBeInTheDocument();
    expect(within(updatedChart).getByText(formatTimingMs(batchMany.medianInferenceMs))).toBeInTheDocument();
    expect(within(updatedChart).getAllByRole("listitem")).toHaveLength(2);

    expect(screen.getByRole("columnheader", { name: "Выбранная эпоха" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "selectedEpoch" })).not.toBeInTheDocument();
    const copy = screen.getByRole("button", { name: "Копировать d44:run:0" });
    copy.focus();
    expect(copy).toHaveFocus();
    fireEvent.click(copy);
    expect(await screen.findByText("Не скопировано")).toBeInTheDocument();
  });
});

describe("timingsComparable", () => {
  const metric: TimingMetric = "medianInferenceMs";

  it("returns no minimum when batch or status differ", () => {
    const mixedBatch = [
      bench("a", { device: "cpu", batchSize: 1, medianInferenceMs: 1 }),
      bench("b", { device: "mps", batchSize: 32, medianInferenceMs: 0.01 }),
    ];
    expect(timingsComparable(mixedBatch, "device", metric)).toBe(false);
    expect(groupMinimum(mixedBatch, "device", metric)).toBeNull();

    const mixedStatus = [
      bench("c", { device: "cpu", status: "ok", medianInferenceMs: 1 }),
      bench("d", { device: "mps", status: "failed", medianInferenceMs: 0.01 }),
    ];
    expect(timingsComparable(mixedStatus, "device", metric)).toBe(false);
    expect(groupMinimum(mixedStatus, "device", metric)).toBeNull();

    const mixedBatchView = render(
      <LatencyComparison rows={mixedBatch} mode="device" metric={metric} title="CPU и MPS" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/Разный batch/);
    expect(screen.queryByRole("list", { name: "CPU и MPS" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/победитель|winner|мин\. в группе|\+\d+%/);
    mixedBatchView.unmount();

    render(<LatencyComparison rows={mixedStatus} mode="device" metric={metric} title="CPU и MPS" />);
    expect(screen.getByRole("status")).toHaveTextContent(/Разный status/);
    expect(screen.queryByRole("list", { name: "CPU и MPS" })).not.toBeInTheDocument();
  });

  it("compares devices only for one model and models only for one device", () => {
    const devices = [
      bench("cpu", { device: "cpu", medianInferenceMs: 0.2, medianForwardBackwardMs: 0.8 }),
      bench("mps", { device: "mps", medianInferenceMs: 0.5, medianForwardBackwardMs: 1.4 }),
    ];
    expect(timingsComparable(devices, "device", metric)).toBe(true);
    expect(timingsComparable(devices, "model", metric)).toBe(false);
    expect(groupMinimum(devices, "device", metric)).toBe(0.2);
    expect(groupMinimum(devices, "device", "medianForwardBackwardMs")).toBe(0.8);

    const models = [
      bench("linear", { modelKind: "linear", device: "cpu", medianInferenceMs: 0.2 }),
      bench("gru", { modelKind: "gru", device: "cpu", medianInferenceMs: 0.4 }),
    ];
    expect(timingsComparable(models, "model", metric)).toBe(true);
    expect(timingsComparable(models, "device", metric)).toBe(false);
    expect(groupMinimum(models, "device", metric)).toBeNull();

    const unmeasured = [devices[0]!, bench("mps-null", { device: "mps", medianInferenceMs: null })];
    expect(timingsComparable(unmeasured, "device", metric)).toBe(false);
    expect(groupMinimum(unmeasured, "device", metric)).toBeNull();
  });

  it("does not treat a same-device mixed batch as one experiment", () => {
    const pane = availableModels();
    const mixed = pane.data.benchmarks.filter(
      (row) => row.modelKind === "linear" && row.device === "cpu" && row.width === 16 && row.context === 8,
    );
    expect(mixed.map((row) => row.batchSize).sort((a, b) => a - b)).toEqual([1, 32]);
    expect(timingsComparable(mixed, "device", metric)).toBe(false);
    expect(groupMinimum(mixed, "device", metric)).toBeNull();

    const matched = rowsForAxis(pane.data.benchmarks, "device", {
      model: "linear",
      device: "cpu",
      shape: "16×8",
      batch: "1",
    });
    expect(matched).toHaveLength(2);
    expect(matched.every((row) => row.batchSize === 1 && row.modelKind === "linear")).toBe(true);
    expect(timingsComparable(matched, "device", metric)).toBe(true);
  });
});

describe("formatTimingMs", () => {
  it("shows не измерено for null", () => {
    expect(formatTimingMs(null)).toBe("не измерено");
  });
});

describe("BenchmarkTimingsTable null fixture", () => {
  it("renders не измерено for null median timings and does not rank them", () => {
    const pane = availableModels();
    const row = { ...pane.data.benchmarks[0]!, medianInferenceMs: null, medianForwardBackwardMs: null };

    render(<BenchmarkTimingsTable benchmarks={[row]} />);

    const table = screen.getByRole("table");
    expect(within(table).getAllByText("не измерено")).toHaveLength(2);
    expect(screen.queryByText(/мин\. в группе|\+\d+%/)).not.toBeInTheDocument();
  });
});
