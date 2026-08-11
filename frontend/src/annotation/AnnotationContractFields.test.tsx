import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AnnotationState, MetaContract } from "../annotationState";
import { AnnotationContractFields } from "./AnnotationContractFields";

const label = (zh: string, en: string) => ({ en, "zh-CN": zh });
const contract: MetaContract = {
  copy_version: "annotation-meta-copy-v1",
  difficulty_options: [
    { code: "trivial", label: label("非常简单", "Trivial / very easy") },
    { code: "occlusion", label: label("遮挡明显", "Occlusion") },
  ],
  schema_version: "annotation-meta-v1",
  scope_options: [{ code: "annotatable", label: label("可标注", "Annotatable") }],
  scope_reason_options: [],
};
const state: AnnotationState = {
  difficulty: [],
  geometry_attempt_reason_text: "",
  geometry_attempt_status: null,
  pairs: [],
  portals: [],
  seam_anchor_pair_id: null,
  scope_reason_codes: [],
  scope_reason_text: "",
  worker_scope_observation: null,
};

describe("AnnotationContractFields MetaSchema v1", () => {
  it("PAP-ANN-SC-010 PAP-ANN-SC-026 renders the selected copy locale and enforces exclusivity", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <AnnotationContractFields contract={contract} onChange={onChange} state={state} />,
    );

    expect(screen.getByLabelText("非常简单")).toBeInTheDocument();
    expect(screen.queryByText("Trivial / very easy")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /Model Issue/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("遮挡明显"));
    expect(onChange).toHaveBeenLastCalledWith({ ...state, difficulty: ["occlusion"] });

    const difficult = { ...state, difficulty: ["occlusion"] };
    rerender(
      <AnnotationContractFields contract={contract} onChange={onChange} state={difficult} />,
    );
    fireEvent.click(screen.getByLabelText("非常简单"));
    expect(onChange).toHaveBeenLastCalledWith({ ...state, difficulty: ["trivial"] });

    rerender(
      <AnnotationContractFields
        contract={contract}
        locale="en"
        onChange={onChange}
        state={state}
      />,
    );
    expect(screen.getByLabelText("Trivial / very easy")).toBeInTheDocument();
    expect(screen.queryByText("非常简单")).not.toBeInTheDocument();
  });

  it("PAP-ANN-SC-010 renders Model Issue only when the frozen contract includes it", () => {
    const onChange = vi.fn();
    const semiContract: MetaContract = {
      ...contract,
      model_issue_options: [
        { code: "acceptable", label: label("模型标注质量好", "Model quality acceptable") },
        { code: "corner_drift", label: label("角点错位或漂移", "Corner drift") },
      ],
    };
    const semiState = { ...state, model_issue: ["corner_drift"] };
    render(
      <AnnotationContractFields contract={semiContract} onChange={onChange} state={semiState} />,
    );

    fireEvent.click(screen.getByLabelText("模型标注质量好"));
    expect(onChange).toHaveBeenLastCalledWith({ ...semiState, model_issue: ["acceptable"] });
  });
});
