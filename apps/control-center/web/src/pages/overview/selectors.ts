import type { Snapshot } from "../../api";

export type SourceQualificationView = {
  recordedVerdict: string;
  sourceName: string;
  commitment: string;
  requests: number;
  accepted: number;
  latencySeconds: number[];
  clockAgeRangeSeconds: number[];
  pools: string[];
  blockers: string[];
  recordedBaseTestsPassed: number;
  recordedCombinedTestsPassed: number;
  qualification: string;
};

export function getSourceQualificationView(
  snapshot: Snapshot,
): SourceQualificationView | null {
  if (snapshot.sourceQualification.availability !== "AVAILABLE") return null;
  const d = snapshot.sourceQualification.data;
  return {
    recordedVerdict: d.recordedVerdict,
    sourceName: d.sourceName,
    commitment: d.commitment,
    requests: d.requests,
    accepted: d.accepted,
    latencySeconds: d.latencySeconds,
    clockAgeRangeSeconds: d.clockAgeRangeSeconds,
    pools: d.pools,
    blockers: d.blockers,
    recordedBaseTestsPassed: d.recordedBaseTestsPassed,
    recordedCombinedTestsPassed: d.recordedCombinedTestsPassed,
    qualification: snapshot.sourceQualification.qualification,
  };
}

export function admissionStatus(snapshot: Snapshot): string | null {
  if (snapshot.research.availability !== "AVAILABLE") return null;
  return snapshot.research.data.admission;
}

export type D43Marker = {
  id: string;
  index: number;
  plannedAt: string;
  access: "DEVELOPMENT" | "PROTECTED";
  recordedStatus: "completed" | "pending" | "failed" | "missed" | null;
  actualStartNs: string | null;
  finishedNs: string | null;
  /** Доля plannedAt между первым и последним окном. Это позиция маркера, не длительность. */
  position: number;
};

export function d43Markers(snapshot: Snapshot): D43Marker[] | null {
  if (snapshot.calendar.availability !== "AVAILABLE") return null;
  const windows = snapshot.calendar.data.windows;
  if (windows.length === 0) return [];
  const times = windows.map((window) => new Date(window.plannedAt).getTime());
  const min = Math.min(...times);
  const span = Math.max(Math.max(...times) - min, 1);
  return windows.map((window) => {
    const position = (new Date(window.plannedAt).getTime() - min) / span;
    if (window.access === "PROTECTED") {
      return {
        id: window.id,
        index: window.index,
        plannedAt: window.plannedAt,
        access: "PROTECTED",
        recordedStatus: null,
        actualStartNs: null,
        finishedNs: null,
        position,
      };
    }
    return {
      id: window.id,
      index: window.index,
      plannedAt: window.plannedAt,
      access: "DEVELOPMENT",
      recordedStatus: window.recordedStatus,
      actualStartNs: window.actualStartNs,
      finishedNs: window.finishedNs,
      position,
    };
  });
}

export function engineeringRunGroups(
  snapshot: Snapshot,
): { kind: string; count: number }[] | null {
  if (snapshot.models.availability !== "AVAILABLE") return null;
  const counts = new Map<string, number>();
  for (const run of snapshot.models.data.runs) {
    counts.set(run.modelKind, (counts.get(run.modelKind) ?? 0) + 1);
  }
  return [...counts.entries()].map(([kind, count]) => ({ kind, count }));
}
