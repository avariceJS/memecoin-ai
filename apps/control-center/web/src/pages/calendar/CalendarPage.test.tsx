import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot, parseSnapshotResponse } from "../../api/parse";
import { appRoutes } from "../../routes";
import { buildPlanCellKinds, D48PlanGrid } from "../corpus/D48PlanGrid";
import { deriveD48Plan } from "../corpus/d48Plan";
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

  it("protected markers omit metric payload", () => {
    renderCalendar();
    const row4 = screen.getByTestId("protected-row-4");
    const row5 = screen.getByTestId("protected-row-5");
    const forbidden = ["180", "174", "135", "finishedNs"];
    for (const token of forbidden) {
      expect(row4.textContent).not.toContain(token);
      expect(row5.textContent).not.toContain(token);
      expect(row4.getAttribute("aria-label")).not.toContain(token);
      expect(row5.getAttribute("aria-label")).not.toContain(token);
    }
    expect(row4).toHaveAccessibleName(/d43:4/);
    expect(row4).toHaveAccessibleName(/доступ закрыт/);
    expect(row5).toHaveAccessibleName(/доступ закрыт/);
    expect(document.querySelector("[data-duration='true']")).toBeNull();
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
    const plan = deriveD48Plan(s);
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.days).toBe(84);
    expect(plan.strata).toEqual(["00:30", "06:30", "12:30", "18:30"]);
    expect(plan.developmentDays).toBe(56);
    expect(plan.reservedDays).toBe(28);
    expect(plan.cells[0]).toMatchObject({ iso: "2026-10-07", kind: "development" });
    expect(plan.cells[56 * 4]).toMatchObject({ iso: "2026-12-02", kind: "reserved" });
    expect(plan.cells[83 * 4]?.iso).toBe("2026-12-29");
  });

  it("renders D48 as day by stratum cells without repeated labels", () => {
    renderCalendar();
    expect(screen.getByTestId("calendar-corpus-sections")).toBeInTheDocument();
    expect(document.querySelector("[data-cc-scroll='d48-day-strata']")).toBeTruthy();
    const cells = screen.getAllByTestId(/^d48-cell-/);
    expect(cells).toHaveLength(336);
    const reserved = screen.getAllByTestId(/d48-cell-reserved-/);
    expect(reserved).toHaveLength(112);
    const forbidden = ["174", "1392", "336", "DESIGN", "PROTECTED"];
    for (const cell of cells) {
      expect(cell.textContent?.trim()).toBe("");
      for (const token of forbidden) {
        expect(cell.textContent).not.toContain(token);
      }
    }
    for (const cell of reserved) {
      expect(cell.getAttribute("aria-label")).toContain("доступ закрыт");
      expect(cell.getAttribute("aria-label")).not.toContain("174");
    }
    const tabbable = cells.filter((cell) => cell.tabIndex === 0);
    expect(tabbable).toHaveLength(1);
    expect(reserved.every((cell) => cell.tabIndex === -1)).toBe(true);
    fireEvent.keyDown(tabbable[0]!, { key: "ArrowRight" });
    const after = screen.getAllByTestId(/^d48-cell-/).filter((cell) => cell.tabIndex === 0);
    expect(after).toHaveLength(1);
    expect(after[0]).not.toBe(tabbable[0]);
    expect(screen.getByText(/336 запланировано/)).toBeInTheDocument();
    expect(screen.queryAllByText("DESIGN")).toHaveLength(0);
    const summary = screen.getByLabelText("Sampling plan counts");
    expect(summary.textContent).toContain("336");
    expect(summary.textContent).toContain("224");
    expect(summary.textContent).toContain("112");
  });

  it("refuses an inconsistent D48 plan instead of drawing a grid", () => {
    const research = snapshotResponse.data.research;
    if (research.availability !== "AVAILABLE") throw new Error("research unavailable");
    render(
      <D48PlanGrid
        sampling={{
          ...research.data.sampling,
          plannedWindows: 10,
        }}
      />,
    );
    expect(screen.getByText("D48_PLAN_INCONSISTENT")).toBeInTheDocument();
    expect(screen.queryByTestId("d48-plan-grid")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByTestId("protected-row-4"));
    const detail = screen.getByRole("complementary", { name: "Детали окна" });
    expect(detail.textContent).toContain("доступ закрыт");
    expect(detail.textContent).not.toContain("174");
    expect(detail.textContent).not.toContain("старт не опубликован");
    expect(detail.textContent).not.toContain("07:54:16");
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
