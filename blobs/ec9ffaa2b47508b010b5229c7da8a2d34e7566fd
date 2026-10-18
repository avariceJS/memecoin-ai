import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability } from "../../api";
import { ReplayPage } from "./ReplayPage";
import styles from "./replay.module.css";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "replay-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderReplay(capabilities: Capability[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["control-center", "snapshot"], snapshotResponse);

  const router = createMemoryRouter(
    [
      {
        path: "/replay",
        element: (
          <QueryClientProvider client={queryClient}>
            <Outlet context={{ snapshotResponse, capabilities }} />
          </QueryClientProvider>
        ),
        children: [{ index: true, element: <ReplayPage /> }],
      },
    ],
    { initialEntries: ["/replay"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("ReplayPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(() => Promise.reject(new Error("fetch should not run")));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows UNAVAILABLE reason from snapshot capability contract", () => {
    const capabilities = snapshotResponse.data.capabilities as Capability[];
    renderReplay(capabilities);

    expect(screen.getByRole("heading", { level: 2, name: /^Replay$/ })).toBeInTheDocument();
    expect(screen.getByText(/CC-P001/)).toBeInTheDocument();
    expect(screen.getByText(/NO_APPROVED_CORE_READ_OR_CONTROL_CONTRACT/)).toBeInTheDocument();
    expect(screen.getByLabelText("UNAVAILABLE")).toBeInTheDocument();
  });

  it("does not expose file import, launch controls, balance, or unrelated evidence", () => {
    const capabilities = snapshotResponse.data.capabilities as Capability[];
    renderReplay(capabilities);

    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByRole("button", { name: /запуст/i })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(/баланс/i)).toBeNull();
    expect(screen.queryByText(/Окна:/)).toBeNull();
    expect(screen.queryByText(/recorded fits/i)).toBeNull();
  });

  it("keeps a long reason inside the local scroll region", () => {
    const long = `NO_APPROVED_${"Y".repeat(240)}`;
    renderReplay([
      {
        id: "replay",
        version: 1,
        availability: "UNAVAILABLE",
        reasonCode: long,
      },
    ]);

    const scroll = document.querySelector("[data-cc-scroll='replay-reason']");
    expect(scroll).toBeTruthy();
    expect(scroll?.classList.contains(styles.codeScroll)).toBe(true);
    expect(scroll?.textContent).toContain(long);
    const card = scroll?.closest("section");
    expect(card?.querySelector("[data-cc-scroll='replay-reason']")).toBe(scroll);
    const leaked = [...(card?.querySelectorAll("p, h2, span") ?? [])].filter(
      (element) => !scroll?.contains(element) && element.textContent?.includes(long),
    );
    expect(leaked).toHaveLength(0);

    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "replay.module.css"), "utf8");
    const block = css.match(/\.codeScroll\s*\{[^}]+\}/)?.[0] ?? "";
    expect(block).toMatch(/overflow:\s*auto/);
    expect(block).toMatch(/max-width:\s*100%/);
    expect(block).toMatch(/min-width:\s*0/);
    expect(block).toMatch(/white-space:\s*nowrap/);
    expect(css).not.toMatch(/min-height:\s*100vh/);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not treat missing replay capability as available", () => {
    const withoutReplay = (snapshotResponse.data.capabilities as Capability[]).filter(
      (c) => c.id !== "replay",
    );
    renderReplay(withoutReplay);

    expect(screen.getByText(/CAPABILITY_NOT_PUBLISHED/)).toBeInTheDocument();
    expect(screen.queryByLabelText("AVAILABLE")).not.toBeInTheDocument();
    expect(screen.getByLabelText("UNAVAILABLE")).toBeInTheDocument();
  });

  it("does not treat incorrectly published AVAILABLE replay as an actionable surface", () => {
    const capabilities: Capability[] = [
      {
        id: "replay",
        version: 1,
        availability: "AVAILABLE",
        mode: "READ_ONLY_RECORDED",
      },
    ];
    renderReplay(capabilities);

    expect(screen.getByText("READ_ONLY_RECORDED")).toBeInTheDocument();
    expect(screen.getByLabelText("LOCKED")).toBeInTheDocument();
    expect(screen.queryByLabelText("AVAILABLE")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not perform POST or PUT HTTP calls from the page", () => {
    const capabilities = snapshotResponse.data.capabilities as Capability[];
    renderReplay(capabilities);

    expect(fetchMock).not.toHaveBeenCalled();
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit | undefined;
      const method = (init?.method ?? "GET").toUpperCase();
      expect(method).not.toBe("POST");
      expect(method).not.toBe("PUT");
    }
  });
});
