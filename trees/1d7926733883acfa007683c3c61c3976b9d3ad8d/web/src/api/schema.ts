import { z } from "zod";
import { ControlCenterApiError } from "./errors";

/** Decimal-string epoch nanoseconds; JSON numbers are rejected. */
export const epochNsSchema = z
  .string()
  .regex(/^\d+$/, { message: "EPOCH_NS_MUST_BE_DECIMAL_STRING" });

export const nullableEpochNsSchema = z.union([epochNsSchema, z.null()]);

const qualificationSchema = z.enum([
  "ENGINEERING_CORPUS",
  "ENGINEERING_ONLY",
  "FROZEN_DESIGN_NOT_ADMISSION",
  "BOUNDED_PROBE_ONLY",
]);

const sourceStampSchema = z
  .object({
    artifactId: z.string(),
    sha256: z.string(),
    coreBaselineCommit: z.string(),
    evidenceDate: z.string(),
    recordedAtNs: nullableEpochNsSchema,
    integrity: z.literal("PINNED_BYTES"),
    temporalScope: z.literal("RECORDED_SNAPSHOT"),
  })
  .passthrough();

const capabilityAvailableSchema = z.object({
  id: z.string(),
  version: z.number(),
  availability: z.literal("AVAILABLE"),
  mode: z.literal("READ_ONLY_RECORDED"),
});

const capabilityUnavailableSchema = z.object({
  id: z.string(),
  version: z.number(),
  availability: z.literal("UNAVAILABLE"),
  reasonCode: z.string(),
});

export const capabilitySchema = z.discriminatedUnion("availability", [
  capabilityAvailableSchema,
  capabilityUnavailableSchema,
]);

const protectedWindowSchema = z
  .object({
    id: z.string(),
    index: z.number(),
    plannedAt: z.string(),
    access: z.literal("PROTECTED"),
  })
  .strict();

const developmentWindowSchema = z.object({
  id: z.string(),
  index: z.number(),
  plannedAt: z.string(),
  access: z.literal("DEVELOPMENT"),
  recordedStatus: z.enum(["completed", "pending", "failed", "missed"]),
  actualStartNs: nullableEpochNsSchema,
  finishedNs: nullableEpochNsSchema,
  firstReceiptAt: z.string().nullable(),
  lastReceiptAt: z.string().nullable(),
  snapshots: z.number().nullable(),
  plannedSnapshots: z.number(),
  usable: z.number().nullable(),
  matureDecisions: z.number().nullable(),
  scenarios: z.number().nullable(),
  corpusExitCode: z.number().nullable(),
  qaExitCode: z.number().nullable(),
  executionOrCollectionExitCode: z.number().nullable(),
  recordedSealSha256: z.string().nullable(),
});

export const windowSchema = z.discriminatedUnion("access", [
  protectedWindowSchema,
  developmentWindowSchema,
]);

const calendarDataSchema = z.object({
  windows: z.array(windowSchema),
  holdoutStartNs: epochNsSchema,
  holdoutEndNs: epochNsSchema,
});

const corpusDataSchema = z.object({
  revision: z.string(),
  developmentWindows: z.number(),
  usableDecisions: z.number(),
  scenarios: z.number(),
  censoredScenarios: z.number(),
  allScenariosObservedDecisions: z.number(),
  greedyNonoverlapDecisions: z.number(),
  nonoverlapIsIndependent: z.literal(false),
  researchPermission: z.literal("NOT_GRANTED_BY_UI"),
});

const researchDataSchema = z.object({
  specId: z.string(),
  recordedProtocolFrozen: z.boolean(),
  recordedResearchLaunchAuthorized: z.boolean(),
  recordedHoldoutReleaseAuthorized: z.boolean(),
  recordedResearchFits: z.number(),
  fitBudget: z.number(),
  recordedHoldoutReleases: z.number(),
  globallyUnseenHoldout: z.literal("NOT_CERTIFIED"),
  engineeringFits: z.number(),
  numericChecks: z.number(),
  numericBlockers: z.number(),
  missingReviews: z.array(z.string()),
  admission: z.literal("LOCKED"),
  holdout: z.literal("PROTECTED"),
  sampling: z.object({
    anchorNs: epochNsSchema,
    developmentEndNs: epochNsSchema,
    holdoutEndNs: epochNsSchema,
    plannedWindows: z.number(),
    developmentWindows: z.number(),
    reservedWindows: z.number(),
    admittedWindows: z.number(),
    recordedCollectionAuthorized: z.boolean(),
    utcOffsets: z.array(z.string()),
  }),
});

const engineeringRunSchema = z.object({
  id: z.string(),
  datasetKind: z.enum(["REAL", "SYNTHETIC"]),
  scope: z.literal("ENGINEERING_SMOKE"),
  modelKind: z.string(),
  seed: z.number(),
  epochs: z.number(),
  parameters: z.number(),
  selectedEpoch: z.number(),
});

const benchmarkSchema = z.object({
  id: z.string(),
  modelKind: z.string(),
  width: z.number(),
  context: z.number(),
  device: z.string(),
  batchSize: z.number(),
  parameters: z.number().nullable(),
  status: z.string(),
  medianInferenceMs: z.number().nullable(),
  medianForwardBackwardMs: z.number().nullable(),
  scope: z.literal("ENGINEERING_BENCHMARK"),
  inputKind: z.literal("SYNTHETIC"),
});

const modelsDataSchema = z.object({
  activeModel: z.null(),
  marketBackboneSelected: z.boolean(),
  runs: z.array(engineeringRunSchema),
  benchmarks: z.array(benchmarkSchema),
});

const sourceQualificationDataSchema = z.object({
  recordedVerdict: z.string(),
  sourceName: z.string(),
  commitment: z.string(),
  requests: z.number(),
  accepted: z.number(),
  latencySeconds: z.array(z.number()),
  clockAgeRangeSeconds: z.array(z.number()),
  pools: z.array(z.string()),
  blockers: z.array(z.string()),
  recordedBaseTestsPassed: z.number(),
  recordedCombinedTestsPassed: z.number(),
});

function paneAvailable<T extends z.ZodType>(dataSchema: T) {
  return z.object({
    availability: z.literal("AVAILABLE"),
    provenance: z.enum(["REAL_EVIDENCE", "DEMO"]),
    qualification: qualificationSchema,
    sourceIds: z.array(z.string()),
    data: dataSchema,
    reasonCode: z.null(),
  });
}

const paneUnavailableSchema = z
  .object({
    availability: z.enum(["UNAVAILABLE", "PROTECTED", "ERROR"]),
    provenance: z.null(),
    qualification: z.null(),
    sourceIds: z.array(z.string()),
    data: z.null(),
    reasonCode: z.string(),
  })
  .strict();

function paneSchema<T extends z.ZodType>(dataSchema: T) {
  return z.discriminatedUnion("availability", [
    paneAvailable(dataSchema),
    paneUnavailableSchema,
  ]);
}

const neverPaneDataSchema = z.never();

export const snapshotSchema = z
  .object({
    apiVersion: z.literal("1.0"),
    mode: z.literal("OBSERVATORY"),
    coreBaselineCommit: z.string(),
    sourcePolicy: z.literal("REVIEWED_PROJECTION_ONLY"),
    sources: z.record(z.string(), sourceStampSchema),
    capabilities: z.array(capabilitySchema),
    calendar: paneSchema(calendarDataSchema),
    corpus: paneSchema(corpusDataSchema),
    research: paneSchema(researchDataSchema),
    models: paneSchema(modelsDataSchema),
    sourceQualification: paneSchema(sourceQualificationDataSchema),
    system: paneSchema(neverPaneDataSchema),
    market: paneSchema(neverPaneDataSchema),
    predictions: paneSchema(neverPaneDataSchema),
    trading: paneSchema(neverPaneDataSchema),
  })
  .passthrough();

export const snapshotResponseSchema = z.object({
  apiVersion: z.literal("1.0"),
  snapshotId: z.string(),
  servedAt: z.string(),
  data: snapshotSchema,
});

export const capabilitiesPayloadSchema = z.object({
  capabilities: z.array(capabilitySchema),
});

export const capabilitiesResponseSchema = z.object({
  apiVersion: z.literal("1.0"),
  snapshotId: z.string(),
  servedAt: z.string(),
  data: capabilitiesPayloadSchema,
});

export function assertSupportedApiMajor(apiVersion: unknown): never | void {
  if (typeof apiVersion !== "string") {
    throw new ControlCenterApiError("UNSUPPORTED_API_MAJOR");
  }
  const major = apiVersion.split(".")[0];
  if (major !== "1") {
    throw new ControlCenterApiError("UNSUPPORTED_API_MAJOR");
  }
}

export type Snapshot = z.infer<typeof snapshotSchema>;
export type SnapshotResponse = z.infer<typeof snapshotResponseSchema>;
export type Capability = z.infer<typeof capabilitySchema>;
