import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSnapshot } from "./client";
import { ControlCenterApiError } from "./errors";

describe("fetchSnapshot", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not treat HTTP 503 as successful empty snapshot", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: async () => ({ error: "ARTIFACT_UNAVAILABLE_OR_INVALID" }),
      }),
    );

    await expect(fetchSnapshot()).rejects.toBeInstanceOf(ControlCenterApiError);
    await expect(fetchSnapshot()).rejects.toMatchObject({
      code: "HTTP_ERROR",
      httpStatus: 503,
    });
  });

  it("sends required boundary header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => ({ error: "ARTIFACT_UNAVAILABLE_OR_INVALID" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchSnapshot().catch(() => undefined);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8787/api/v1/snapshot",
      expect.objectContaining({
        method: "GET",
        credentials: "omit",
        headers: { "X-Control-Center": "1" },
      }),
    );
  });
});
