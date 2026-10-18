import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ControlCenterApiError } from "./errors";
import { parseSnapshot } from "./parse";

const fixturePath = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../published/observatory-v1.json",
);

function loadFixture(): Record<string, unknown> {
  return JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>;
}

describe("parseSnapshot", () => {
  it("parses published observatory-v1.json", () => {
    const snapshot = parseSnapshot(loadFixture());
    expect(snapshot.apiVersion).toBe("1.0");
    expect(snapshot.calendar.availability).toBe("AVAILABLE");
  });

  it("preserves window0 counts and null actualStartNs", () => {
    const snapshot = parseSnapshot(loadFixture());
    if (snapshot.calendar.availability !== "AVAILABLE") {
      throw new Error("expected available calendar");
    }
    const w0 = snapshot.calendar.data.windows[0];
    expect(w0.access).toBe("DEVELOPMENT");
    if (w0.access !== "DEVELOPMENT") return;
    expect(w0.plannedSnapshots).toBe(180);
    expect(w0.snapshots).toBe(180);
    expect(w0.usable).toBe(174);
    expect(w0.matureDecisions).toBe(135);
    expect(w0.actualStartNs).toBeNull();
  });

  it("accepts protected windows without snapshot fields", () => {
    const snapshot = parseSnapshot(loadFixture());
    if (snapshot.calendar.availability !== "AVAILABLE") {
      throw new Error("expected available calendar");
    }
    const w4 = snapshot.calendar.data.windows[4];
    const w5 = snapshot.calendar.data.windows[5];
    expect(w4).toEqual({
      access: "PROTECTED",
      id: "d43:4",
      index: 4,
      plannedAt: "2026-10-04T19:53:59.502126+00:00",
    });
    expect(w5.access).toBe("PROTECTED");
    expect(Object.keys(w5).sort()).toEqual(["access", "id", "index", "plannedAt"]);
  });

  it("rejects apiVersion 2.0", () => {
    const raw = { ...loadFixture(), apiVersion: "2.0" };
    expect(() => parseSnapshot(raw)).toThrow();
  });

  it("rejects epoch nanoseconds as JSON numbers", () => {
    const raw = structuredClone(loadFixture());
    const cal = raw.calendar as Record<string, unknown>;
    const data = cal.data as Record<string, unknown>;
    data.holdoutStartNs = 1791143639502126000;
    expect(() => parseSnapshot(raw)).toThrow();
  });

  it("rejects unavailable pane with data array instead of null", () => {
    const raw = structuredClone(loadFixture());
    raw.system = {
      availability: "UNAVAILABLE",
      data: [],
      provenance: null,
      qualification: null,
      reasonCode: "NO_CURRENT_SERVICE_OR_HARDWARE_FEED",
      sourceIds: [],
    };
    expect(() => parseSnapshot(raw)).toThrow();
  });

  it("throws ControlCenterApiError for unsupported major", () => {
    expect(() => parseSnapshot({ apiVersion: "2.0" })).toThrow(ControlCenterApiError);
    try {
      parseSnapshot({ apiVersion: "2.0" });
    } catch (err) {
      expect((err as ControlCenterApiError).code).toBe("UNSUPPORTED_API_MAJOR");
    }
  });
});
