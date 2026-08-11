import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import {
  type AnnotationState,
  type MetaContract,
  SCOPE_REASON_CODES,
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
const v1Golden = JSON.parse(
  readFileSync(resolve(process.cwd(), "../tests/annotation_state_v1_golden.json"), "utf-8"),
) as GoldenVector & { difficulty_order: string[]; model_issue_order: string[] };

describe("canonical AnnotationState", () => {
  test("matches the shared Python and JavaScript golden vector", async () => {
    expect(canonicalAnnotationJson(golden.state)).toBe(golden.canonical_json);
    await expect(annotationStateSha(golden.state)).resolves.toBe(golden.state_sha);
  });

  test("matches the shared versioned metadata golden vector", async () => {
    const option = (code: string) => ({ code, label: { en: code, "zh-CN": code } });
    const contract: MetaContract = {
      copy_version: "annotation-meta-copy-v1",
      difficulty_options: v1Golden.difficulty_order.map(option),
      model_issue_options: v1Golden.model_issue_order.map(option),
      schema_version: "annotation-meta-v1",
      scope_options: [],
      scope_reason_options: SCOPE_REASON_CODES.map(option),
    };
    expect(canonicalAnnotationJson(v1Golden.state, contract)).toBe(v1Golden.canonical_json);
    await expect(annotationStateSha(v1Golden.state, contract)).resolves.toBe(v1Golden.state_sha);
  });

  test("orders scope reasons by the Task metadata contract", () => {
    const option = (code: string) => ({ code, label: { en: code, "zh-CN": code } });
    const contract: MetaContract = {
      copy_version: "future-copy",
      difficulty_options: [],
      schema_version: "future-schema",
      scope_options: [],
      scope_reason_options: ["other", "non_manhattan"].map(option),
    };
    const state: AnnotationState = {
      ...golden.state,
      scope_reason_codes: ["non_manhattan", "other"],
    };

    expect(JSON.parse(canonicalAnnotationJson(state, contract)).scope_reason_codes).toEqual([
      "other",
      "non_manhattan",
    ]);
  });
});
