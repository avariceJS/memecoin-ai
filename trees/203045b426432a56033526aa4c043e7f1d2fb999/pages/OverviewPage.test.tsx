import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../api/parse";
import type { Capability } from "../api";
import { OverviewPage } from "./OverviewPage";
import { getSourceQualificationView } from "./overview";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "observatory-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderOverview(data = snapshotResponse.data) {
  const response = { ...snapshotResponse, data };
  const capabilities = data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Outlet context={{ snapshotResponse: response, capabilities }} />,
        children: [{ index: true, element: <OverviewPage /> }],
      },
    ],
    { initialEntries: ["/"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders six latency columns and two-value clock range without live health", () => {
    renderOverview();

    const qual = snapshotResponse.data.sourceQualification;
    expect(qual.availability).toBe("AVAILABLE");
    if (qual.availability !== "AVAILABLE") throw new Error("expected available qualification");
    const latency = qual.data.latencySeconds;
    expect(latency).toHaveLength(6);

    expect(screen.getAllByText(/с$/).length).toBeGreaterThanOrEqual(6);
    expect(screen.getByText(/NOT_READY_FOR_ACTIVATION/)).toBeInTheDocument();
    expect(screen.getByText(/p95 не опубликован/)).toBeInTheDocument();
    expect(screen.queryByText(/system online/i)).not.toBeInTheDocument();
    expect(screen.getByText(/481/)).toBeInTheDocument();
    expect(screen.getByText(/502/)).toBeInTheDocument();
    expect(screen.getByText(/Запросы probe 6\/6/)).toBeInTheDocument();

    const clockRange = qual.data.clockAgeRangeSeconds;
    expect(clockRange).toHaveLength(2);
    expect(screen.getByText(/9\.950–10\.768/)).toBeInTheDocument();

    const bodyText = document.body.textContent ?? "";
    expect(bodyText.match(/\bp95\b/gi)?.length ?? 0).toBe(1);
    expect(screen.getAllByText("0/114").length).toBeGreaterThan(0);
    expect(screen.getAllByText("135").length).toBeGreaterThan(0);
    expect(screen.getAllByText("15").length).toBeGreaterThan(0);
    expect(screen.getAllByText("6").length).toBeGreaterThan(0);
  });

  it("renders six D43 markers and does not invent a duration without a start", () => {
    renderOverview();

    for (const id of ["d43:0", "d43:1", "d43:2", "d43:3", "d43:4", "d43:5"]) {
      const marker = screen.getByRole("button", { name: new RegExp(id) });
      expect(marker.getAttribute("style") ?? "").not.toMatch(/width/);
    }
    expect(screen.getByText(/Старт не опубликован\. Длительность не строится\./)).toBeInTheDocument();
    expect(screen.getAllByText(/Допуск к исследованию не выдан/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/system online/i)).not.toBeInTheDocument();
  });

  it("does not label a demo corpus as real evidence", () => {
    const data = structuredClone(snapshotResponse.data);
    if (data.corpus.availability !== "AVAILABLE") throw new Error("expected corpus");
    data.corpus.provenance = "DEMO";
    renderOverview(data);

    expect(screen.getByText("DEMO — вымышленные данные")).toBeInTheDocument();
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
  });

  it("copies pool id via clipboard without network", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderOverview();

    const qualification = getSourceQualificationView(snapshotResponse.data);
    if (!qualification) throw new Error("expected available qualification");
    const poolId = qualification.pools[0]!;
    const copyButtons = screen.getAllByRole("button", { name: /Копировать pool id/i });
    fireEvent.click(copyButtons[0]!);

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(poolId);
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows a local failure when clipboard rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("not focused", "NotAllowedError"));
    Object.assign(navigator, { clipboard: { writeText } });

    renderOverview();
    const copyButtons = screen.getAllByRole("button", { name: /Копировать pool id/i });
    fireEvent.click(copyButtons[0]!);

    expect(await screen.findByText("Не скопировано")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
