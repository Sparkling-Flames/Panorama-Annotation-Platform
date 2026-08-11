import { useEffect, useState } from "react";

import type { AnnotationState } from "../annotationState";

type Corner = "bottom_left" | "bottom_right" | "top_left" | "top_right";
type PortalDraft = Record<`${Corner}_${"u" | "v"}`, string> & {
  evidence_status: string;
  host_edge_ref: string;
  kind: string;
};

const MAX_U = 0.9999999999999999;
const PORTAL_KINDS = [
  "door",
  "architectural_opening",
  "window",
  "open_connection",
  "unknown",
] as const;
const PORTAL_EVIDENCE = ["direct_visible", "inferred", "ambiguous"] as const;
const EMPTY_PORTAL: PortalDraft = {
  bottom_left_u: "",
  bottom_left_v: "",
  bottom_right_u: "",
  bottom_right_v: "",
  evidence_status: "ambiguous",
  host_edge_ref: "",
  kind: "unknown",
  top_left_u: "",
  top_left_v: "",
  top_right_u: "",
  top_right_v: "",
};

export function PortalEditor({
  onEditingChange,
  onChange,
  state,
}: {
  onEditingChange?: (editing: boolean) => void;
  onChange: (state: AnnotationState) => void;
  state: AnnotationState;
}) {
  const [draft, setDraft] = useState<PortalDraft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => onEditingChange?.(draft !== null), [draft, onEditingChange]);

  function edit(portal: AnnotationState["portals"][number]): void {
    setEditingId(portal.portal_id);
    setDraft({
      bottom_left_u: String(portal.geometry.bottom_left.u),
      bottom_left_v: String(portal.geometry.bottom_left.v),
      bottom_right_u: String(portal.geometry.bottom_right.u),
      bottom_right_v: String(portal.geometry.bottom_right.v),
      evidence_status: portal.evidence_status,
      host_edge_ref: portal.host_edge_ref ?? "",
      kind: portal.kind,
      top_left_u: String(portal.geometry.top_left.u),
      top_left_v: String(portal.geometry.top_left.v),
      top_right_u: String(portal.geometry.top_right.u),
      top_right_v: String(portal.geometry.top_right.v),
    });
    setError(false);
  }

  function save(): void {
    if (draft === null) return;
    const coordinate = (corner: Corner, axis: "u" | "v") => Number(draft[`${corner}_${axis}`]);
    const geometry = {
      bottom_left: { u: coordinate("bottom_left", "u"), v: coordinate("bottom_left", "v") },
      bottom_right: { u: coordinate("bottom_right", "u"), v: coordinate("bottom_right", "v") },
      top_left: { u: coordinate("top_left", "u"), v: coordinate("top_left", "v") },
      top_right: { u: coordinate("top_right", "u"), v: coordinate("top_right", "v") },
    };
    const rawCoordinates = [
      draft.top_left_u,
      draft.top_left_v,
      draft.top_right_u,
      draft.top_right_v,
      draft.bottom_left_u,
      draft.bottom_left_v,
      draft.bottom_right_u,
      draft.bottom_right_v,
    ];
    const valid =
      rawCoordinates.every((value) => value !== "") &&
      Object.values(geometry).every(
        ({ u, v }) =>
          Number.isFinite(u) && Number.isFinite(v) && 0 <= u && u < 1 && 0 <= v && v <= 1,
      );
    if (!valid) {
      setError(true);
      return;
    }
    const portal = {
      evidence_status: draft.evidence_status,
      geometry,
      host_edge_ref: draft.host_edge_ref || null,
      kind: draft.kind,
      portal_id: editingId ?? crypto.randomUUID(),
    };
    onChange({
      ...state,
      portals:
        editingId === null
          ? [...state.portals, portal]
          : state.portals.map((current) => (current.portal_id === editingId ? portal : current)),
    });
    setDraft(null);
    setEditingId(null);
    setError(false);
  }

  return (
    <section aria-label="Portal 编辑">
      <h3>Portal observations</h3>
      <ul>
        {state.portals.map((portal, index) => (
          <li id={`portal-${portal.portal_id}`} key={portal.portal_id}>
            Portal {index + 1}: {portal.kind} / {portal.evidence_status}
            <button onClick={() => edit(portal)} type="button">
              编辑 Portal {index + 1}
            </button>
            <button
              onClick={() =>
                onChange({
                  ...state,
                  portals: state.portals.filter((item) => item.portal_id !== portal.portal_id),
                })
              }
              type="button"
            >
              删除 Portal {index + 1}
            </button>
          </li>
        ))}
      </ul>
      {draft === null ? (
        <button
          onClick={() => {
            setEditingId(null);
            setDraft({ ...EMPTY_PORTAL });
          }}
          type="button"
        >
          添加 Portal
        </button>
      ) : (
        <div>
          <label>
            Portal 类型
            <select
              aria-label="Portal 类型"
              onChange={(event) => setDraft({ ...draft, kind: event.currentTarget.value })}
              value={draft.kind}
            >
              {PORTAL_KINDS.map((kind) => (
                <option key={kind}>{kind}</option>
              ))}
            </select>
          </label>
          <label>
            Portal 证据状态
            <select
              aria-label="Portal 证据状态"
              onChange={(event) =>
                setDraft({ ...draft, evidence_status: event.currentTarget.value })
              }
              value={draft.evidence_status}
            >
              {PORTAL_EVIDENCE.map((evidence) => (
                <option key={evidence}>{evidence}</option>
              ))}
            </select>
          </label>
          <label>
            Portal host edge
            <select
              onChange={(event) => setDraft({ ...draft, host_edge_ref: event.currentTarget.value })}
              value={draft.host_edge_ref}
            >
              <option value="">未关联 / None</option>
              {state.pairs.map((pair, index) => (
                <option key={pair.pair_id} value={pair.pair_id}>
                  第 {index + 1} 对
                </option>
              ))}
            </select>
          </label>
          {(
            [
              ["top_left", "左上"],
              ["top_right", "右上"],
              ["bottom_left", "左下"],
              ["bottom_right", "右下"],
            ] as const
          ).flatMap(([corner, label]) =>
            (["u", "v"] as const).map((axis) => (
              <label key={`${corner}-${axis}`}>
                {`Portal ${label} ${axis}`}
                <input
                  aria-label={`Portal ${label} ${axis}`}
                  max={axis === "u" ? MAX_U : 1}
                  min={0}
                  onChange={(event) =>
                    setDraft({ ...draft, [`${corner}_${axis}`]: event.currentTarget.value })
                  }
                  step="any"
                  type="number"
                  value={draft[`${corner}_${axis}`]}
                />
              </label>
            )),
          )}
          {error ? <p role="alert">Portal 坐标必须完整且位于规范化范围内。</p> : null}
          <button onClick={save} type="button">
            保存 Portal
          </button>
          <button
            onClick={() => {
              setDraft(null);
              setEditingId(null);
              setError(false);
            }}
            type="button"
          >
            取消 Portal
          </button>
        </div>
      )}
    </section>
  );
}
