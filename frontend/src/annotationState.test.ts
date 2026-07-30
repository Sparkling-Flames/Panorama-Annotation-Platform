import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import {
  type AnnotationState,
  annotationStateSha,
  canonicalAnnotationJson,
} from "./annotationState";

type GoldenVector = {
  canonical_json: string;
  state: AnnotationState;
  state_sha: string;
};

const golden = JSON.parse(
  readFileSync(resolve(process.cwd(), "../tests/annotation_state_golden.json"), "utf-8"),
) as GoldenVector;

describe("canonical AnnotationState", () => {
  test("matches the shared Python and JavaScript golden vector", async () => {
    expect(canonicalAnnotationJson(golden.state)).toBe(golden.canonical_json);
    await expect(annotationStateSha(golden.state)).resolves.toBe(golden.state_sha);
  });
});
