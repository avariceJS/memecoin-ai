import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

function installQueries(
  response: typeof snapshotResponse | undefined,
  options?: { error?: Error },
) {
  const refetchSnapshot = vi.fn();
  const refetchCapabilities = vi.fn();
  if (options?.error) {
    vi.mocked(useSnapshot).mockReturnValue({
      isLoading: false,
      isError: true,
      isSuccess: false,
      isFetching: false,
      data: undefined,
      error: options.error,
      refetch: refetchSnapshot,
    } as unknown as ReturnType<typeof useSnapshot>);
  } else {
    vi.mocked(useSnapshot).mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      isFetching: false,
      data: response,
      error: null,
      refetch: refetchSnapshot,
    } as unknown as ReturnType<typeof useSnapshot>);
  }
  vi.mocked(useCapabilities).mockReturnValue({
    isLoading: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    data: response?.data.capabilities ?? [],
    error: null,
    refetch: refetchCapabilities,
  } as unknown as ReturnType<typeof useCapabilities>);
  return { refetchSnapshot, refetchCapabilities };
}

describe("AppShell", () => {
  it("shows grouped nav, one recorded status, and manual refresh", () => {
    const { refetchSnapshot, refetchCapabilities } = installQueries(snapshotResponse);
    renderShell("/");

    expect(screen.getByText("Memecoin AI")).toBeInTheDocument();
    expect(screen.getByText("Observatory")).toBeInTheDocument();
    expect(screen.getByText("Свидетельства")).toBeInTheDocument();
    expect(screen.getByText("Дальше")).toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "Разделы" });
    expect(
      [...nav.querySelectorAll("a")].map((link) => link.textContent?.trim()),
    ).toEqual([
      "Обзор",
      "Календарь",
      "Источники",
      "Исследование",
      "Модели",
      "Система",
      "Replay",
      "Торговля",
    ]);
    const marks = new Set(
      [...nav.querySelectorAll("svg")].map((icon) => icon.innerHTML),
    );
    expect(marks.size).toBe(8);

    expect(screen.getAllByText("RECORDED EVIDENCE")).toHaveLength(1);
    expect(screen.getByText("Состояние core недоступно")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/DEMO/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Live Mode/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/System Online/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(refetchSnapshot).toHaveBeenCalledTimes(1);
    expect(refetchCapabilities).toHaveBeenCalledTimes(1);
  });

  it("exposes primary nav links for keyboard focus", () => {
    installQueries(snapshotResponse);
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
    const { refetchSnapshot, refetchCapabilities } = installQueries(undefined, {
      error: new ControlCenterApiError("FETCH_FAILED"),
    });
    renderShell("/");

    expect(screen.getByText("FETCH_FAILED")).toBeInTheDocument();
    expect(screen.queryByText(/DEMO/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/RECORDED EVIDENCE/i)).not.toBeInTheDocument();
    expect(screen.getByText("Нет записи")).toBeInTheDocument();
    expect(screen.getByText("Состояние core недоступно")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(refetchSnapshot).toHaveBeenCalledTimes(1);
    expect(refetchCapabilities).toHaveBeenCalledTimes(1);
  });

  it("does not call a demo snapshot recorded evidence", () => {
    const data = structuredClone(snapshotResponse.data);
    for (const key of ["calendar", "corpus", "research", "models", "sourceQualification"] as const) {
      const pane = data[key];
      if (pane.availability === "AVAILABLE") pane.provenance = "DEMO";
    }
    installQueries({ ...snapshotResponse, snapshotId: "demo-snapshot-fixture", data });
    renderShell("/");

    const header = screen.getByRole("banner");
    expect(within(header).getByText("DEMO — вымышленные данные")).toBeInTheDocument();
    expect(within(header).queryByText(/RECORDED EVIDENCE/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/RECORDED EVIDENCE/i)).not.toBeInTheDocument();
  });

  it("reveals a long snapshot id from the keyboard", () => {
    const longId = `cc02-shell-${"9f3a7c1e".repeat(8)}-tail`;
    installQueries({ ...snapshotResponse, snapshotId: longId });
    renderShell("/");

    const control = screen.getByRole("button", { name: `snapshot ${longId}` });
    expect(control).not.toHaveAttribute("tabindex", "-1");
    expect(control.textContent ?? "").toContain("…");
    expect(control.textContent ?? "").not.toContain(longId);
    expect(screen.queryByText(longId)).not.toBeInTheDocument();
    control.focus();
    expect(control).toHaveFocus();
    fireEvent.keyDown(control, { key: "Enter" });
    expect(screen.getByText(longId)).toBeVisible();
  });
});
