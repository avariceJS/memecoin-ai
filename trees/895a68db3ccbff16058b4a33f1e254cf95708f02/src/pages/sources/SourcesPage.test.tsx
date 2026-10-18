import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability } from "../../api";
import { SourcesPage } from "./SourcesPage";
import { getSourceQualificationView } from "../overview/selectors";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "sources-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderSources() {
  const capabilities = snapshotResponse.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/sources",
        element: <Outlet context={{ snapshotResponse, capabilities }} />,
        children: [{ index: true, element: <SourcesPage /> }],
      },
    ],
    { initialEntries: ["/sources"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("SourcesPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders stamps, latency table in seconds, and disclaims live RPC health", () => {
    renderSources();

    expect(screen.getByRole("heading", { name: /Sources \/ Pools/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Source stamps/i })).toBeInTheDocument();
    expect(screen.getAllByText(/^PINNED_BYTES$/).length).toBeGreaterThanOrEqual(9);
    expect(screen.getAllByText(/время не опубликовано/).length).toBeGreaterThanOrEqual(8);
    expect(screen.getByText(/NOT_READY_FOR_ACTIVATION/)).toBeInTheDocument();
    expect(screen.getAllByText(/Alchemy HTTP/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/finalized/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/6\/6 не означает текущий RPC health/)).toBeInTheDocument();
    expect(screen.getByText(/Запросы probe 6\/6/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Recorded tests stage/i })).toBeInTheDocument();
    expect(screen.getByText(/481\/502/)).toBeInTheDocument();
    expect(screen.getByText(/9\.950–10\.768/)).toBeInTheDocument();
    expect(screen.queryByText(/system online/i)).not.toBeInTheDocument();

    const qual = snapshotResponse.data.sourceQualification;
    if (qual.availability !== "AVAILABLE") throw new Error("expected qualification");
    expect(qual.data.latencySeconds).toHaveLength(6);

    expect(screen.getAllByText(/^с$/).length).toBeGreaterThanOrEqual(6);
  });

  it("copies pool id via clipboard without network", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderSources();

    const qualification = getSourceQualificationView(snapshotResponse.data);
    if (!qualification) throw new Error("expected qualification");
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
    renderSources();
    fireEvent.click(screen.getAllByRole("button", { name: /Копировать pool id/i })[0]!);
    expect(await screen.findByText("Не скопировано")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
