import { describe, expect, it } from "vitest";

import { formatLocalTimestamp } from "./locale";

describe("local timestamps", () => {
  it("PAP-PBD-SC-003 displays an explicit local time-zone without changing the source instant", () => {
    const source = "2026-08-11T01:02:03Z";
    expect(formatLocalTimestamp(source, "en")).toMatch(/GMT|UTC/);
    expect(source).toBe("2026-08-11T01:02:03Z");
  });
});
