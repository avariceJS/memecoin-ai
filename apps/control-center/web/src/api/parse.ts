import { ControlCenterApiError } from "./errors";
import {
  assertSupportedApiMajor,
  capabilitiesResponseSchema,
  snapshotResponseSchema,
  snapshotSchema,
  type Capability,
  type Snapshot,
  type SnapshotResponse,
} from "./schema";

function toValidationError(): ControlCenterApiError {
  return new ControlCenterApiError("SCHEMA_VALIDATION_FAILED");
}

export function parseSnapshot(raw: unknown): Snapshot {
  if (raw !== null && typeof raw === "object" && "apiVersion" in raw) {
    assertSupportedApiMajor((raw as { apiVersion: unknown }).apiVersion);
  }
  const result = snapshotSchema.safeParse(raw);
  if (!result.success) {
    throw toValidationError();
  }
  return result.data;
}

export function parseSnapshotResponse(raw: unknown): SnapshotResponse {
  if (raw !== null && typeof raw === "object" && "apiVersion" in raw) {
    assertSupportedApiMajor((raw as { apiVersion: unknown }).apiVersion);
  }
  const result = snapshotResponseSchema.safeParse(raw);
  if (!result.success) {
    throw toValidationError();
  }
  return result.data;
}

export function parseCapabilities(raw: unknown): Capability[] {
  if (raw !== null && typeof raw === "object" && "apiVersion" in raw) {
    assertSupportedApiMajor((raw as { apiVersion: unknown }).apiVersion);
  }
  const result = capabilitiesResponseSchema.safeParse(raw);
  if (!result.success) {
    throw toValidationError();
  }
  return result.data.data.capabilities;
}
