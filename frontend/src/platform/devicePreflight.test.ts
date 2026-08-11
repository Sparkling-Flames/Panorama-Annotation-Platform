import { describe, expect, it } from "vitest";

import { evaluateDevicePreflight } from "./devicePreflight";

const supported = {
  hasAbortController: true,
  hasCryptoRandomUuid: true,
  hasFetch: true,
  hasIndexedDb: true,
  hasWebGl: true,
  hasWebLocks: true,
  screenWidth: 1440,
  userAgent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
};

describe("device preflight", () => {
  it("accepts desktop Chrome and Edge with required APIs", () => {
    expect(evaluateDevicePreflight(supported)).toEqual({ blockers: [], warnings: [] });
    expect(
      evaluateDevicePreflight({
        ...supported,
        userAgent: supported.userAgent.replace("Chrome", "Edg"),
      }),
    ).toEqual({ blockers: [], warnings: [] });
  });

  it("blocks unsupported browsers, missing required APIs, and narrow screens", () => {
    const result = evaluateDevicePreflight({
      ...supported,
      hasWebLocks: false,
      screenWidth: 800,
      userAgent: "Mozilla/5.0 Firefox/141.0",
    });
    expect(result.blockers).toEqual([
      "unsupported_browser",
      "screen_too_narrow",
      "web_locks_unavailable",
    ]);
  });

  it("PAP-PBD-SC-002 degrades without WebGL or IndexedDB instead of blocking the POC", () => {
    expect(evaluateDevicePreflight({ ...supported, hasIndexedDb: false, hasWebGl: false })).toEqual(
      { blockers: [], warnings: ["indexed_db_unavailable", "webgl_unavailable"] },
    );
  });
});
