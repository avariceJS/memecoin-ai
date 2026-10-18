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
