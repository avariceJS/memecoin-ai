/** UI DTO v1. Epoch nanoseconds MUST remain decimal strings, never JS numbers. */
export type EpochNs = string;
export type Qualification = 'ENGINEERING_CORPUS' | 'ENGINEERING_ONLY' |
  'FROZEN_DESIGN_NOT_ADMISSION' | 'BOUNDED_PROBE_ONLY';
export type Pane<T> = {
  availability: 'AVAILABLE'; provenance: 'REAL_EVIDENCE' | 'DEMO';
  qualification: Qualification; sourceIds: string[]; data: T; reasonCode: null;
} | {
  availability: 'UNAVAILABLE' | 'PROTECTED' | 'ERROR'; provenance: null;
  qualification: null; sourceIds: string[]; data: null; reasonCode: string;
};
export interface SourceStamp {
  artifactId: string; sha256: string; coreBaselineCommit: string;
  evidenceDate: string; recordedAtNs: EpochNs | null;
  integrity: 'PINNED_BYTES'; temporalScope: 'RECORDED_SNAPSHOT';
}
export type Capability = {
  id: string; version: number; availability: 'AVAILABLE'; mode: 'READ_ONLY_RECORDED';
} | { id: string; version: number; availability: 'UNAVAILABLE'; reasonCode: string };
export interface WindowPlan { id: string; index: number; plannedAt: string }
export type Window = (WindowPlan & { access: 'PROTECTED' }) | (WindowPlan & {
  access: 'DEVELOPMENT'; recordedStatus: 'completed' | 'pending' | 'failed' | 'missed';
  actualStartNs: EpochNs | null; finishedNs: EpochNs | null;
  firstReceiptAt: string | null; lastReceiptAt: string | null;
  snapshots: number | null; plannedSnapshots: number; usable: number | null;
  matureDecisions: number | null; scenarios: number | null;
  corpusExitCode: number | null; qaExitCode: number | null;
  executionOrCollectionExitCode: number | null; recordedSealSha256: string | null;
});
export interface Calendar {
  windows: Window[]; holdoutStartNs: EpochNs; holdoutEndNs: EpochNs;
}
export interface Corpus {
  revision: string; developmentWindows: number; usableDecisions: number; scenarios: number;
  censoredScenarios: number; allScenariosObservedDecisions: number;
  greedyNonoverlapDecisions: number; nonoverlapIsIndependent: false;
  researchPermission: 'NOT_GRANTED_BY_UI';
}
export interface Research {
  specId: string; recordedProtocolFrozen: boolean; recordedResearchLaunchAuthorized: boolean;
  recordedHoldoutReleaseAuthorized: boolean; recordedResearchFits: number; fitBudget: number;
  recordedHoldoutReleases: number; globallyUnseenHoldout: 'NOT_CERTIFIED';
  engineeringFits: number; numericChecks: number; numericBlockers: number;
  missingReviews: string[]; admission: 'LOCKED'; holdout: 'PROTECTED';
  sampling: { anchorNs: EpochNs; developmentEndNs: EpochNs; holdoutEndNs: EpochNs;
    plannedWindows: number; developmentWindows: number; reservedWindows: number;
    admittedWindows: number; recordedCollectionAuthorized: boolean; utcOffsets: string[] };
}
export interface EngineeringRun {
  id: string; datasetKind: 'REAL' | 'SYNTHETIC'; scope: 'ENGINEERING_SMOKE';
  modelKind: string; seed: number; epochs: number; parameters: number; selectedEpoch: number;
}
export interface Benchmark {
  id: string; modelKind: string; width: number; context: number; device: string;
  batchSize: number; parameters: number | null; status: string;
  medianInferenceMs: number | null; medianForwardBackwardMs: number | null;
  scope: 'ENGINEERING_BENCHMARK'; inputKind: 'SYNTHETIC';
}
export interface Models {
  activeModel: null; marketBackboneSelected: boolean;
  runs: EngineeringRun[]; benchmarks: Benchmark[];
}
export interface SourceQualification {
  recordedVerdict: string; sourceName: string; commitment: string; requests: number;
  accepted: number; latencySeconds: number[]; clockAgeRangeSeconds: number[];
  pools: string[]; blockers: string[]; recordedBaseTestsPassed: number;
  recordedCombinedTestsPassed: number;
}
export interface Snapshot {
  apiVersion: '1.0'; mode: 'OBSERVATORY'; coreBaselineCommit: string;
  sourcePolicy: 'REVIEWED_PROJECTION_ONLY'; sources: Record<string,SourceStamp>;
  capabilities: Capability[]; calendar: Pane<Calendar>; corpus: Pane<Corpus>;
  research: Pane<Research>; models: Pane<Models>; sourceQualification: Pane<SourceQualification>;
  system: Pane<never>; market: Pane<never>; predictions: Pane<never>; trading: Pane<never>;
}
export interface SnapshotResponse {
  apiVersion: '1.0'; snapshotId: string; servedAt: string; data: Snapshot;
}
/** No generic command, path, shell, source-root, RPC URL or admission API exists. */
