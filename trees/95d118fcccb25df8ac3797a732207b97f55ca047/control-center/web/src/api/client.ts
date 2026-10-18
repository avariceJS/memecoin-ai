import { ControlCenterApiError, type ApiErrorCode } from "./errors";
import { parseCapabilities, parseSnapshotResponse } from "./parse";
import type { Capability, SnapshotResponse } from "./schema";

export const API_BASE_URL = "http://127.0.0.1:8787";

const CONTROL_CENTER_HEADER = { "X-Control-Center": "1" } as const;

function assertAllowedGetUrl(url: string): void {
  const parsed = new URL(url);
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    parsed.port !== "8787" ||
    parsed.username ||
    parsed.password
  ) {
    throw new ControlCenterApiError("DISALLOWED_URL");
  }
}

function errorCodeFromBody(body: unknown): ApiErrorCode {
  if (
    body !== null &&
    typeof body === "object" &&
    "error" in body &&
    typeof (body as { error: unknown }).error === "string"
  ) {
    return "HTTP_ERROR";
  }
  return "HTTP_ERROR";
}

async function getJson(path: string): Promise<unknown> {
  const url = `${API_BASE_URL}${path}`;
  assertAllowedGetUrl(url);

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: CONTROL_CENTER_HEADER,
      credentials: "omit",
    });
  } catch {
    throw new ControlCenterApiError("FETCH_FAILED");
  }

  const contentType = response.headers.get("Content-Type") ?? "";
  const isJson =
    contentType.includes("application/json") || contentType.includes("+json");

  let body: unknown;
  try {
    if (!isJson) {
      throw new ControlCenterApiError("NON_JSON_RESPONSE", response.status);
    }
    body = await response.json();
  } catch (err) {
    if (err instanceof ControlCenterApiError) {
      throw err;
    }
    throw new ControlCenterApiError("NON_JSON_RESPONSE", response.status);
  }

  if (!response.ok) {
    throw new ControlCenterApiError(errorCodeFromBody(body), response.status);
  }

  return body;
}

export async function fetchSnapshot(): Promise<SnapshotResponse> {
  const body = await getJson("/api/v1/snapshot");
  return parseSnapshotResponse(body);
}

export async function fetchCapabilities(): Promise<Capability[]> {
  const body = await getJson("/api/v1/capabilities");
  return parseCapabilities(body);
}
