export type AnnotationPoint = {
  point_id: string;
  u: number;
  v: number;
};

export type AnnotationPointPair = {
  pair_id: string;
  order_index: number;
  top: AnnotationPoint;
  bottom: AnnotationPoint;
};

export type PortalObservation = {
  portal_id: string;
  kind: string;
  geometry: {
    top_left: Pick<AnnotationPoint, "u" | "v">;
    top_right: Pick<AnnotationPoint, "u" | "v">;
    bottom_left: Pick<AnnotationPoint, "u" | "v">;
    bottom_right: Pick<AnnotationPoint, "u" | "v">;
  };
  evidence_status: string;
  host_edge_ref: string | null;
};

export type AnnotationState = {
  difficulty?: string[];
  geometry_attempt_reason_text: string;
  geometry_attempt_status: string | null;
  model_issue?: string[];
  pairs: AnnotationPointPair[];
  portals: PortalObservation[];
  seam_anchor_pair_id: string | null;
  scope_reason_codes: string[];
  scope_reason_text: string;
  worker_scope_observation: string | null;
};

export type MetaOption = {
  code: string;
  label: { en: string; "zh-CN": string };
};

export type MetaContract = {
  copy_version: string;
  difficulty_options: MetaOption[];
  model_issue_options?: MetaOption[];
  schema_version: string;
  scope_options: MetaOption[];
  scope_reason_options: MetaOption[];
};

export const SCOPE_REASON_CODES = [
  "non_manhattan",
  "multi_level_floor",
  "multi_level_ceiling",
  "internal_void",
  "camera_cell_ambiguous",
  "portal_ambiguous",
  "insufficient_evidence",
  "severe_image_artifact",
  "other",
];

function orderedCodes(codes: string[], options?: MetaOption[]): string[] {
  if (options === undefined) return [...codes];
  const selected = new Set(codes);
  return options.map((option) => option.code).filter((code) => selected.has(code));
}

export function canonicalAnnotationJson(state: AnnotationState, contract?: MetaContract): string {
  const pairs = [...state.pairs]
    .sort((left, right) => left.order_index - right.order_index)
    .map((pair) => ({
      bottom: {
        point_id: pair.bottom.point_id,
        u: pair.bottom.u,
        v: pair.bottom.v,
      },
      order_index: pair.order_index,
      pair_id: pair.pair_id,
      top: {
        point_id: pair.top.point_id,
        u: pair.top.u,
        v: pair.top.v,
      },
    }));
  const portals = [...state.portals]
    .sort((left, right) => left.portal_id.localeCompare(right.portal_id))
    .map((portal) => ({
      evidence_status: portal.evidence_status,
      geometry: {
        bottom_left: portal.geometry.bottom_left,
        bottom_right: portal.geometry.bottom_right,
        top_left: portal.geometry.top_left,
        top_right: portal.geometry.top_right,
      },
      host_edge_ref: portal.host_edge_ref,
      kind: portal.kind,
      portal_id: portal.portal_id,
    }));
  const scope_reason_codes =
    contract === undefined
      ? [...state.scope_reason_codes].sort(
          (left, right) => SCOPE_REASON_CODES.indexOf(left) - SCOPE_REASON_CODES.indexOf(right),
        )
      : orderedCodes(state.scope_reason_codes, contract.scope_reason_options);
  return JSON.stringify({
    ...(state.difficulty === undefined
      ? {}
      : { difficulty: orderedCodes(state.difficulty, contract?.difficulty_options) }),
    geometry_attempt_reason_text: state.geometry_attempt_reason_text.trim(),
    geometry_attempt_status: state.geometry_attempt_status,
    pairs,
    portals,
    seam_anchor_pair_id: state.seam_anchor_pair_id,
    scope_reason_codes,
    scope_reason_text: state.scope_reason_text.trim(),
    worker_scope_observation: state.worker_scope_observation,
    ...(state.model_issue === undefined
      ? {}
      : { model_issue: orderedCodes(state.model_issue, contract?.model_issue_options) }),
  });
}

export function annotationStateSubmissionReady(state: AnnotationState): boolean {
  if (state.geometry_attempt_status === null || state.worker_scope_observation === null)
    return false;
  if (state.geometry_attempt_status === "best_effort_complete" && state.pairs.length === 0)
    return false;
  if (
    state.geometry_attempt_status === "not_drawable" &&
    state.geometry_attempt_reason_text.trim() === ""
  )
    return false;
  if (state.worker_scope_observation !== "annotatable" && state.scope_reason_codes.length === 0)
    return false;
  if (state.difficulty !== undefined && state.difficulty.length === 0) return false;
  if (state.model_issue !== undefined && state.model_issue.length === 0) return false;
  return !state.scope_reason_codes.includes("other") || state.scope_reason_text.trim() !== "";
}

export async function annotationStateSha(
  state: AnnotationState,
  contract?: MetaContract,
): Promise<string> {
  const input = new TextEncoder().encode(canonicalAnnotationJson(state, contract));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
