import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability } from "../../api";
import { SystemPage } from "./SystemPage";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "system-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderSystem(queryClient: QueryClient) {
  const capabilities = snapshotResponse.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/system",
        element: (
          <QueryClientProvider client={queryClient}>
            <Outlet context={{ snapshotResponse, capabilities }} />
          </QueryClientProvider>
        ),
        children: [{ index: true, element: <SystemPage /> }],
      },
    ],
    { initialEntries: ["/system"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("SystemPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("fetch should not run"))));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows adapter response, core pane UNAVAILABLE, recorded stage disclaimer, future surfaces", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["control-center", "snapshot"], snapshotResponse);

    renderSystem(queryClient);

    expect(screen.getByRole("heading", { level: 2, name: /Система — границы/i })).toBeInTheDocument();
    expect(screen.getByText(/UI adapter ответил/)).toBeInTheDocument();
    expect(screen.getByText(/system-fixture-snapshot/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-03T12:00:00.000Z/)).toBeInTheDocument();
    expect(screen.getAllByText(/NO_CURRENT_SERVICE_OR_HARDWARE_FEED/).length).toBeGreaterThan(0);
    expect(screen.getByText(/не текущий процесс/i)).toBeInTheDocument();

    const qual = snapshotResponse.data.sourceQualification;
    if (qual.availability !== "AVAILABLE") throw new Error("expected qualification");
    expect(
      screen.getByText(new RegExp(`${qual.data.recordedBaseTestsPassed}/${qual.data.recordedCombinedTestsPassed}`)),
    ).toBeInTheDocument();

    const emergencyRow = screen.getByText(/^emergencyStop$/).closest("li");
    expect(emergencyRow).toBeTruthy();
    expect(emergencyRow?.querySelector('[aria-label="UNAVAILABLE"]')).toBeInTheDocument();
    expect(screen.getAllByText(/NO_APPROVED_CORE_READ_OR_CONTROL_CONTRACT/).length).toBeGreaterThan(1);
    expect(screen.queryByText(/^system online$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("does not show adapter healthy when snapshot query failed", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryDefaults(["control-center", "snapshot"], {
      queryFn: () => Promise.reject(new Error("network down")),
    });
    void queryClient.prefetchQuery({ queryKey: ["control-center", "snapshot"] });

    renderSystem(queryClient);

    await waitFor(() => {
      expect(screen.getByText(/UI adapter не подтверждён/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/UI adapter ответил/)).not.toBeInTheDocument();
  });
});
