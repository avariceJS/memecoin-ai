import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import { parseSnapshot } from "../../api/parse";
import type { Capability } from "../../api";
import { SourcesPage } from "./SourcesPage";
import { formatClockAgeRangeSeconds, formatProbeSeconds } from "./format";
import { shortenPoolId } from "./viewModel";

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

const blockerLabels = [
  "План аккаунта и жёсткий нулевой лимит не проверены",
  "Соответствие runtime-учётных данных и billing-аккаунта не проверено",
  "Календарный сервис и время бодрствования не квалифицированы",
  "Полное live-окно не квалифицировано",
  "Отдельное решение об активации отсутствует",
];

function renderSources(response: typeof snapshotResponse = snapshotResponse) {
  const capabilities = response.data.capabilities as Capability[];
  const router = createMemoryRouter(
    [
      {
        path: "/sources",
        element: <Outlet context={{ snapshotResponse: response, capabilities }} />,
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

  it("показывает записанную схему и не называет текущую связность", () => {
    renderSources();
    const topology = screen.getByRole("region", { name: "Источник → квалификация → проекция" });

    expect(topology).toHaveTextContent("Записанная схема");
    expect(topology).toHaveTextContent("не показывают поток данных");
    expect(topology).toHaveTextContent("Alchemy HTTP");
    expect(topology).toHaveTextContent("finalized");
    expect(topology).toHaveTextContent("Дата наблюдения 2026-10-03.");
    expect(topology).toHaveTextContent("Ограниченный probe");
    expect(topology).toHaveTextContent("Не готово к активации");
    expect(topology).toHaveTextContent("6/6");
    expect(topology).toHaveTextContent("не текущий RPC health");
    expect(topology).toHaveTextContent("Проверенная проекция");
    expect(topology.textContent ?? "").not.toMatch(/связност/i);
    expect(topology.textContent ?? "").not.toMatch(/system online|live feed|подключен/i);
    expect(topology.querySelectorAll("[data-edge='recorded']")).toHaveLength(2);
    expect(screen.queryByText(/system online/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("REAL EVIDENCE").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("DEMO — вымышленные данные")).not.toBeInTheDocument();
  });

  it("читает блокеры по-человечески и не монтирует сырой код, пока закрыто", () => {
    renderSources();
    const blockers = document.querySelector("[data-cc-region='source-blockers']");
    expect(blockers).not.toBeNull();

    for (const label of blockerLabels) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("account_plan_and_zero_dollar_hard_cap_unverified")).not.toBeInTheDocument();
    expect(screen.queryByText("separate_activation_decision_absent")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Сырой код" }));
    const codes = document.querySelector("[data-cc-scroll='source-blocker-codes']");
    expect(codes).toHaveTextContent("account_plan_and_zero_dollar_hard_cap_unverified");
    expect(codes).toHaveTextContent("separate_activation_decision_absent");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("прячет неизвестный код блокера за той же подписью", () => {
    const data = structuredClone(snapshotResponse.data);
    const pane = data.sourceQualification;
    if (pane.availability !== "AVAILABLE") {
      throw new Error("published fixture must expose AVAILABLE source qualification");
    }
    pane.data.blockers = ["not_a_known_blocker_code"];
    renderSources({ ...snapshotResponse, data });

    expect(screen.getByText("Подробности недоступны")).toBeInTheDocument();
    expect(screen.queryByText("not_a_known_blocker_code")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сырой код" }));
    expect(screen.getByText("not_a_known_blocker_code")).toBeInTheDocument();
  });

  it("копирует полный pool id и держит его в локальной прокрутке", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderSources();

    const pane = snapshotResponse.data.sourceQualification;
    if (pane.availability !== "AVAILABLE") throw new Error("expected qualification");
    const poolId = pane.data.pools[0]!;
    const shortId = shortenPoolId(poolId);
    const region = document.querySelector("[data-cc-region='pool-ids']");
    const scroll = region?.querySelector("[data-cc-scroll='pool-id']");

    expect(screen.getByText(shortId)).toBeInTheDocument();
    expect(shortId).not.toBe(poolId);
    expect(scroll).toHaveTextContent(poolId);

    fireEvent.click(screen.getAllByRole("button", { name: /Копировать pool id/i })[0]!);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(poolId);
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("показывает локальный отказ clipboard", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("not focused", "NotAllowedError"));
    Object.assign(navigator, { clipboard: { writeText } });
    renderSources();
    fireEvent.click(screen.getAllByRole("button", { name: /Копировать pool id/i })[0]!);
    expect(await screen.findByText("Не скопировано")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("рисует только опубликованные замеры и не строит объём", () => {
    renderSources();
    const pane = snapshotResponse.data.sourceQualification;
    if (pane.availability !== "AVAILABLE") throw new Error("expected qualification");

    expect(screen.getByRole("heading", { name: "Замеры latency" })).toBeInTheDocument();
    expect(screen.getByText(/Объём не опубликован/)).toBeInTheDocument();
    expect(screen.getByText(/не ось времени/)).toBeInTheDocument();
    expect(document.querySelector("polyline")).toBeNull();
    expect(document.querySelector("animate")).toBeNull();
    for (const seconds of pane.data.latencySeconds) {
      expect(screen.getByText(formatProbeSeconds(seconds))).toBeInTheDocument();
    }
    expect(screen.getAllByText("с").length).toBeGreaterThanOrEqual(pane.data.latencySeconds.length);
    expect(screen.getByText(formatClockAgeRangeSeconds(pane.data.clockAgeRangeSeconds))).toBeInTheDocument();
    expect(screen.getByText("481")).toBeInTheDocument();
    expect(screen.getByText("502")).toBeInTheDocument();
    expect(screen.getByText(/p95 не опубликован/)).toBeInTheDocument();
  });

  it("открывает provenance с клавиатуры и возвращает фокус", async () => {
    renderSources();
    const trigger = screen.getByRole("button", { name: "Штампы и хеши" });
    const hash = snapshotResponse.data.sources.d43?.sha256;
    if (!hash) throw new Error("expected d43 hash");

    expect(screen.queryByText(hash)).not.toBeInTheDocument();
    expect(screen.queryByText("время не опубликовано")).not.toBeInTheDocument();

    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });
    const dialog = await screen.findByRole("dialog", { name: "Штампы проекции" });
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(screen.getAllByText("PINNED_BYTES").length).toBeGreaterThanOrEqual(9);
    expect(screen.getAllByText("время не опубликовано").length).toBeGreaterThanOrEqual(8);
    expect(screen.getByText(hash)).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Закрыть" })).toHaveFocus();
    const hashes = dialog.querySelectorAll("[data-cc-scroll='stamp-hash']");
    const last = hashes[hashes.length - 1] as HTMLElement;
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Закрыть" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });
    expect(screen.queryByText(hash)).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("maps DEMO provenance to the demo badge, not synthetic input", () => {
    const data = structuredClone(snapshotResponse.data);
    const pane = data.sourceQualification;
    if (pane.availability !== "AVAILABLE") {
      throw new Error("published fixture must expose AVAILABLE source qualification");
    }
    pane.provenance = "DEMO";
    renderSources({ ...snapshotResponse, data });

    expect(screen.getAllByText("DEMO — вымышленные данные").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("SYNTHETIC INPUT")).not.toBeInTheDocument();
    expect(screen.queryByText("REAL EVIDENCE")).not.toBeInTheDocument();
  });
});
