import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot, parseSnapshotResponse } from "../../api/parse";
import { appRoutes } from "../../routes";
import { WindowDetail } from "./CalendarPage";
import type { DevelopmentWindow } from "./labels";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

function loadSnapshotResponse() {
  const snapshot = parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>);
  return parseSnapshotResponse({
    apiVersion: "1.0",
    snapshotId: "calendar-test-snapshot",
    servedAt: "2026-10-03T12:00:00.000Z",
    data: snapshot,
  });
}

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    useSnapshot: vi.fn(),
    useCapabilities: vi.fn(),
  };
});

import { useCapabilities, useSnapshot } from "../../api";

const snapshotResponse = loadSnapshotResponse();

function renderCalendar() {
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

  const router = createMemoryRouter(appRoutes, { initialEntries: ["/calendar"] });
  return render(<RouterProvider router={router} />);
}

function developmentWindow0(): DevelopmentWindow {
  const pane = snapshotResponse.data.calendar;
  if (pane.availability !== "AVAILABLE") throw new Error("calendar unavailable");
  const w = pane.data.windows[0];
  if (w.access !== "DEVELOPMENT") throw new Error("expected development window");
  return w;
}

describe("CalendarPage fixture", () => {
  it("parses published snapshot with window0 counts and null actualStartNs", () => {
    const w0 = developmentWindow0();
    expect(w0.plannedSnapshots).toBe(180);
    expect(w0.snapshots).toBe(180);
    expect(w0.usable).toBe(174);
    expect(w0.matureDecisions).toBe(135);
    expect(w0.actualStartNs).toBeNull();
  });

  it("does not label pending windows as missed", () => {
    renderCalendar();
    expect(screen.getAllByText("ожидание").length).toBeGreaterThan(0);
    expect(screen.queryByText("пропущено")).not.toBeInTheDocument();
    expect(screen.queryByText("missed")).not.toBeInTheDocument();
  });

  it("protected rows omit metric payload in DOM", () => {
    renderCalendar();
    const row4 = screen.getByTestId("protected-row-4");
    const row5 = screen.getByTestId("protected-row-5");
    const forbidden = ["180", "174", "135", "finishedNs"];
    for (const token of forbidden) {
      expect(row4.textContent).not.toContain(token);
      expect(row5.textContent).not.toContain(token);
    }
    expect(row4.textContent).toContain("d43:4");
    expect(row5.textContent).toContain("доступ закрыт");
  });

  it("keyboard selection of window 0 shows first receipt and unpublished start", () => {
    renderCalendar();
    const w0 = developmentWindow0();
    const lane = screen.getByRole("button", { name: /#0/ });
    lane.focus();
    fireEvent.keyDown(lane, { key: "Enter" });
    fireEvent.click(lane);
    expect(screen.getByText("старт не опубликован")).toBeInTheDocument();
    expect(w0.firstReceiptAt).toBeTruthy();
    expect(screen.getByText(/07:54:16/)).toBeInTheDocument();
  });
});

describe("mixed stage fixture", () => {
  it("shows corpus pass and execution fail separately", () => {
    const base = developmentWindow0();
    const mixed: DevelopmentWindow = {
      ...base,
      corpusExitCode: 0,
      executionOrCollectionExitCode: 1,
    };
    render(<WindowDetail window={mixed} zone="UTC" />);
    expect(screen.getByText(/corpus: успех/i)).toBeInTheDocument();
    expect(screen.getByText(/execution: код 1 \(неуспех\)/i)).toBeInTheDocument();
  });
});
