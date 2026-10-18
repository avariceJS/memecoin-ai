import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability, SnapshotResponse } from "../../api";
import { SystemPage } from "./SystemPage";
import styles from "./system.module.css";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../published/observatory-v1.json",
);

const cssPath = join(dirname(fileURLToPath(import.meta.url)), "system.module.css");

const snapshotResponse: SnapshotResponse = {
  apiVersion: "1.0",
  snapshotId: "system-fixture-snapshot",
  servedAt: "2026-10-03T12:00:00.000Z",
  data: parseSnapshot(JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>),
};

function renderSystem(
  queryClient: QueryClient,
  capabilities: Capability[] = snapshotResponse.data.capabilities,
  response: SnapshotResponse = snapshotResponse,
) {
  const router = createMemoryRouter(
    [
      {
        path: "/system",
        element: (
          <QueryClientProvider client={queryClient}>
            <Outlet context={{ snapshotResponse: response, capabilities }} />
          </QueryClientProvider>
        ),
        children: [{ index: true, element: <SystemPage /> }],
      },
    ],
    { initialEntries: ["/system"] },
  );
  return render(<RouterProvider router={router} />);
}

function readyClient(response: SnapshotResponse = snapshotResponse) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["control-center", "snapshot"], response);
  return queryClient;
}

describe("SystemPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("fetch should not run"))));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("показывает матрицу, ответ adapter и отдельное неизвестное состояние core", () => {
    renderSystem(readyClient());

    expect(screen.getByRole("heading", { level: 2, name: /Границы возможностей/i })).toBeInTheDocument();
    const region = document.querySelector("[data-cc-region='capability-cards']");
    expect(region).toBeTruthy();
    expect(region?.closest("aside")).toBeNull();
    const scrolls = region?.querySelectorAll("[data-cc-scroll^='capability-']").length ?? 0;
    const toggles = region?.querySelectorAll("button").length ?? 0;
    expect(scrolls + toggles).toBe(snapshotResponse.data.capabilities.length);

    const adapter = document.getElementById("adapter-reach-heading")?.closest("section");
    const core = document.getElementById("core-runtime-heading")?.closest("section");
    expect(adapter?.textContent).toMatch(/UI adapter ответил/);
    expect(adapter?.textContent).toMatch(/system-fixture-snapshot/);
    expect(adapter?.textContent).toMatch(/2026-10-03T12:00:00\.000Z/);
    expect(adapter?.textContent).not.toMatch(/NO_CURRENT_SERVICE_OR_HARDWARE_FEED/);
    expect(core?.textContent).not.toMatch(/UI adapter ответил/);
    expect(core?.textContent).toMatch(/NO_CURRENT_SERVICE_OR_HARDWARE_FEED/);
    expect(core?.querySelector("[aria-label='UNAVAILABLE']")).toBeTruthy();

    expect(screen.getByText(/не текущий процесс/i)).toBeInTheDocument();
    const qual = snapshotResponse.data.sourceQualification;
    if (qual.availability !== "AVAILABLE") throw new Error("expected qualification");
    expect(
      screen.getByText(`${qual.data.recordedBaseTestsPassed}/${qual.data.recordedCombinedTestsPassed}`),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("REAL EVIDENCE")).toBeInTheDocument();
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("DEMO — вымышленные данные")).not.toBeInTheDocument();

    const emergencyRow = screen.getByText("Аварийная остановка").closest("li");
    expect(emergencyRow).toBeTruthy();
    expect(emergencyRow?.querySelector('[aria-label="UNAVAILABLE"]')).toBeInTheDocument();
    expect(screen.queryByText(/^emergencyStop$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/NO_APPROVED_CORE_READ_OR_CONTROL_CONTRACT/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^system online$/i)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button").every((button) => button.textContent === "Код контракта")).toBe(true);
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it("не подменяет provenance DEMO на synthetic или real", () => {
    const data = structuredClone(snapshotResponse.data);
    if (data.sourceQualification.availability !== "AVAILABLE") {
      throw new Error("expected qualification");
    }
    data.sourceQualification.provenance = "DEMO";
    const response = { ...snapshotResponse, data };
    renderSystem(readyClient(response), response.data.capabilities, response);

    const recorded = document.getElementById("recorded-core-tests-heading")?.closest("section");
    expect(recorded?.querySelector("[aria-label='DEMO — вымышленные данные']")).toBeTruthy();
    expect(screen.queryByLabelText("REAL EVIDENCE")).not.toBeInTheDocument();
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button").every((button) => button.textContent === "Код контракта")).toBe(true);
  });

  it("держит длинный код внутри прокрутки карточки", () => {
    const long = `NO_APPROVED_${"Y".repeat(240)}`;
    const capabilities = snapshotResponse.data.capabilities.map((cap) =>
      cap.id === "emergencyStop" && cap.availability === "UNAVAILABLE"
        ? { ...cap, reasonCode: long }
        : cap,
    );
    renderSystem(readyClient(), capabilities);

    const stop = screen.getByText("Аварийная остановка").closest("li");
    if (!(stop instanceof HTMLElement)) throw new Error("expected emergency card");
    fireEvent.click(within(stop).getByRole("button", { name: "Код контракта" }));
    const scroll = stop?.querySelector("[data-cc-scroll='capability-emergencyStop']");
    expect(scroll).toBeTruthy();
    expect(scroll?.classList.contains(styles.codeScroll)).toBe(true);
    expect(scroll?.textContent).toContain(long);
    const card = scroll?.closest("li");
    expect(card?.querySelector("[data-cc-scroll='capability-emergencyStop']")).toBe(scroll);
    const leaked = [...(card?.querySelectorAll("p, span, h3, h4") ?? [])].filter(
      (element) => !scroll?.contains(element) && element.textContent?.includes(long),
    );
    expect(leaked).toHaveLength(0);

    const css = readFileSync(cssPath, "utf8");
    expect(css).toMatch(/\.codeScroll,\s*\.pinScroll\s*\{[^}]*min-width:\s*0;/);
    expect(css).toMatch(/\.codeScroll,\s*\.pinScroll\s*\{[^}]*max-width:\s*100%;/);
    expect(css).toMatch(/\.codeScroll,\s*\.pinScroll\s*\{[^}]*overflow:\s*auto;/);
    expect(css).toMatch(/\.codeScroll\s*\{[^}]*white-space:\s*normal;/);
    expect(css).toMatch(/\.codeScroll\s*\{[^}]*overflow-wrap:\s*anywhere;/);
    expect(css).toMatch(
      /\.matrix\s*\{[^}]*repeat\(auto-fill,\s*minmax\(min\(100%,\s*240px\),\s*1fr\)\)/,
    );
    expect(css).not.toMatch(/minmax\(260px,\s*320px\)/);
    expect(screen.getAllByRole("button").every((button) => button.textContent === "Код контракта")).toBe(true);
  });

  it("не открывает действие, если управляющая возможность опубликована как чтение", () => {
    const capabilities: Capability[] = [
      {
        id: "live",
        version: 1,
        availability: "AVAILABLE",
        mode: "READ_ONLY_RECORDED",
      },
    ];
    renderSystem(readyClient(), capabilities);

    const card = screen.getByText(/^Live$/).closest("li");
    expect(card?.querySelector("[aria-label='LOCKED']")).toBeTruthy();
    expect(card?.textContent).toMatch(/READ_ONLY_RECORDED/);
    expect(screen.queryByLabelText("AVAILABLE")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /запуск|торговля|health/i })).not.toBeInTheDocument();
  });

  it("не считает adapter ответившим, когда запрос snapshot упал", async () => {
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
    const core = document.getElementById("core-runtime-heading")?.closest("section");
    expect(core?.querySelector("[aria-label='UNAVAILABLE']")).toBeTruthy();
    expect(core?.textContent).toMatch(/NO_CURRENT_SERVICE_OR_HARDWARE_FEED/);
    expect(screen.getAllByRole("button").every((button) => button.textContent === "Код контракта")).toBe(true);
  });
});
