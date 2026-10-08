import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot, parseSnapshotResponse } from "../../api/parse";
import { appRoutes } from "../../routes";
import { buildPlanCellKinds } from "../corpus/D48PlanGrid";
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

  it("parses corpus and D48 sampling counts from published fixture", () => {
    const corpus = snapshotResponse.data.corpus;
    const research = snapshotResponse.data.research;
    expect(corpus.availability).toBe("AVAILABLE");
    expect(research.availability).toBe("AVAILABLE");
    if (corpus.availability !== "AVAILABLE" || research.availability !== "AVAILABLE") {
      throw new Error("expected available panes");
    }
    expect(corpus.data.revision).toBe("000000");
    expect(corpus.data.developmentWindows).toBe(1);
    expect(corpus.data.usableDecisions).toBe(174);
    expect(corpus.data.scenarios).toBe(1392);
    expect(corpus.data.censoredScenarios).toBe(362);
    expect(corpus.data.allScenariosObservedDecisions).toBe(122);
    expect(corpus.data.greedyNonoverlapDecisions).toBe(5);
    expect(corpus.data.nonoverlapIsIndependent).toBe(false);
    expect(corpus.data.researchPermission).toBe("NOT_GRANTED_BY_UI");
    const s = research.data.sampling;
    expect(s.plannedWindows).toBe(336);
    expect(s.developmentWindows).toBe(224);
    expect(s.reservedWindows).toBe(112);
    expect(s.admittedWindows).toBe(0);
    expect(s.recordedCollectionAuthorized).toBe(false);
    expect(s.utcOffsets).toEqual(["00:30", "06:30", "12:30", "18:30"]);
    expect(research.qualification).toBe("FROZEN_DESIGN_NOT_ADMISSION");
    expect(research.data.admission).toBe("LOCKED");
    expect(buildPlanCellKinds(s)).toHaveLength(336);
  });

  it("renders D48 grid with protected cells free of corpus metric tokens", () => {
    renderCalendar();
    expect(screen.getByTestId("calendar-corpus-sections")).toBeInTheDocument();
    expect(screen.getByTestId("d48-plan-grid").children).toHaveLength(336);
    const reserved = screen.getAllByTestId(/d48-cell-reserved-/);
    expect(reserved).toHaveLength(112);
    const forbidden = ["174", "1392", "336"];
    for (const cell of reserved) {
      for (const token of forbidden) {
        expect(cell.textContent).not.toContain(token);
      }
      expect(cell.textContent).toContain("доступ закрыт");
    }
    expect(screen.getByText(/336 запланировано/)).toBeInTheDocument();
    const summary = screen.getByLabelText("Sampling plan counts");
    expect(summary.textContent).toContain("336");
    expect(summary.textContent).toContain("224");
    expect(summary.textContent).toContain("112");
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
