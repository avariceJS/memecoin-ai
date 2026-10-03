import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability } from "../../api";
import { ResearchPage } from "./ResearchPage";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const snapshotResponse = {
  apiVersion: "1.0" as const,
  snapshotId: "research-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderResearch() {
  const capabilities = snapshotResponse.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/research",
        element: <Outlet context={{ snapshotResponse, capabilities }} />,
        children: [{ index: true, element: <ResearchPage /> }],
      },
    ],
    { initialEntries: ["/research"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("ResearchPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("fetch should not run"))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders spec, gates, counters without permission or percent readiness", () => {
    renderResearch();

    expect(screen.getByRole("heading", { name: /Исследование \(recorded\)/i })).toBeInTheDocument();
    expect(screen.getByText("F01-v1")).toBeInTheDocument();
    expect(screen.getByText("0/114")).toBeInTheDocument();
    expect(screen.getAllByText("135").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("113").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("111").length).toBeGreaterThanOrEqual(1);

    expect(screen.getAllByText("LOCKED").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("PROTECTED")).toBeInTheDocument();
    expect(screen.getByText("NOT_CERTIFIED")).toBeInTheDocument();

    expect(screen.getByText(/не global exposures/)).toBeInTheDocument();
    expect(screen.getByText(/не proof globally unseen/)).toBeInTheDocument();
    expect(screen.getByText(/prospective_sampling_binding/)).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /Открыть календарь/i })).toHaveAttribute(
      "href",
      "/calendar",
    );
    expect(screen.getByText(/CC-P001 — безопасный operational metadata export/)).toBeInTheDocument();

    const body = document.body.textContent ?? "";
    expect(body.toLowerCase()).not.toMatch(/permission granted/);
    expect(body).not.toMatch(/разрешено/i);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(body).not.toMatch(/%\s*ready|percent ready|готовность.*%/i);
  });
});
