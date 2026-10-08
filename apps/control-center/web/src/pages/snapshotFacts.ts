import type { Snapshot, SnapshotResponse } from "../api";

export function truncateId(id: string, head = 8, tail = 6): string {
  if (id.length <= head + tail + 1) return id;
  return `${id.slice(0, head)}…${id.slice(-tail)}`;
}

export function calendarWindowCount(snapshot: Snapshot): number | null {
  if (snapshot.calendar.availability !== "AVAILABLE") return null;
  return snapshot.calendar.data.windows.length;
}

export function recordedFits(snapshot: Snapshot): { recorded: number; budget: number } | null {
  if (snapshot.research.availability !== "AVAILABLE") return null;
  const { recordedResearchFits, fitBudget } = snapshot.research.data;
  return { recorded: recordedResearchFits, budget: fitBudget };
}

export function engineeringFits(snapshot: Snapshot): number | null {
  if (snapshot.research.availability !== "AVAILABLE") return null;
  return snapshot.research.data.engineeringFits;
}

export function runCount(snapshot: Snapshot): number | null {
  if (snapshot.models.availability !== "AVAILABLE") return null;
  return snapshot.models.data.runs.length;
}

export function recordedVerdict(snapshot: Snapshot): string | null {
  if (snapshot.sourceQualification.availability !== "AVAILABLE") return null;
  return snapshot.sourceQualification.data.recordedVerdict;
}

export function primaryEvidenceDate(snapshot: Snapshot): string | null {
  const stamps = Object.values(snapshot.sources);
  if (stamps.length === 0) return null;
  return stamps[0]?.evidenceDate ?? null;
}

export type SnapshotView = {
  response: SnapshotResponse;
  snapshot: Snapshot;
};
