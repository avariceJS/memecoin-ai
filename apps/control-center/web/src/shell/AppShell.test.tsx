import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router";
import { ControlCenterApiError } from "../api/errors";
import { parseSnapshot } from "../api/parse";
import { appRoutes } from "../routes";

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

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    useSnapshot: vi.fn(),
    useCapabilities: vi.fn(),
  };
});

import { useCapabilities, useSnapshot } from "../api";

function renderShell(initialPath = "/") {
  const router = createMemoryRouter(appRoutes, { initialEntries: [initialPath] });
  return render(<RouterProvider router={router} />);
}

describe("AppShell", () => {
  it("shows nav, RECORDED banner, and overview markers", () => {
    vi.mocked(useSnapshot).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: snapshotResponse,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSnapshot>);
    vi.mocked(useCapabilities).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: snapshotResponse.data.capabilities,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCapabilities>);

    renderShell("/");

    expect(screen.getByRole("link", { name: "Обзор" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Торговля" })).toBeInTheDocument();
    expect(screen.getAllByText(/RECORDED EVIDENCE/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/DEMO/i)).not.toBeInTheDocument();
  });

  it("exposes primary nav links for keyboard focus", () => {
    vi.mocked(useSnapshot).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: snapshotResponse,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSnapshot>);
    vi.mocked(useCapabilities).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: snapshotResponse.data.capabilities,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCapabilities>);

    renderShell("/");

    const overview = screen.getByRole("link", { name: "Обзор" });
    const trading = screen.getByRole("link", { name: "Торговля" });
    expect(overview.tagName).toBe("A");
    expect(trading.tagName).toBe("A");
    expect(overview).not.toHaveAttribute("tabindex", "-1");
    expect(trading).not.toHaveAttribute("tabindex", "-1");
    overview.focus();
    expect(overview).toHaveFocus();
    trading.focus();
    expect(trading).toHaveFocus();
  });

  it("shows safe error code on snapshot failure", () => {
    vi.mocked(useSnapshot).mockReturnValue({
      isLoading: false,
      isError: true,
      isSuccess: false,
      data: undefined,
      error: new ControlCenterApiError("FETCH_FAILED"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSnapshot>);
    vi.mocked(useCapabilities).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: [],
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCapabilities>);

    renderShell("/");

    expect(screen.getByText("FETCH_FAILED")).toBeInTheDocument();
    expect(screen.queryByText(/DEMO/i)).not.toBeInTheDocument();
  });
});
