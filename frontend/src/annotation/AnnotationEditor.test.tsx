import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationState } from "../annotationState";
import { AnnotationEditor } from "./AnnotationEditor";

const initialState: AnnotationState = {
  geometry_attempt_reason_text: "",
  geometry_attempt_status: "best_effort_complete",
  pairs: [
    {
      bottom: {
        point_id: "00000000-0000-4000-8000-000000000002",
        u: 0.12,
        v: 0.88,
      },
      order_index: 0,
      pair_id: "00000000-0000-4000-8000-000000000001",
      top: {
        point_id: "00000000-0000-4000-8000-000000000003",
        u: 0.1,
        v: 0.08,
      },
    },
    {
      bottom: {
        point_id: "00000000-0000-4000-8000-000000000005",
        u: 0.82,
        v: 0.9,
      },
      order_index: 1,
      pair_id: "00000000-0000-4000-8000-000000000004",
      top: {
        point_id: "00000000-0000-4000-8000-000000000006",
        u: 0.8,
        v: 0.1,
      },
    },
  ],
  portals: [],
  seam_anchor_pair_id: "00000000-0000-4000-8000-000000000001",
  scope_reason_codes: [],
  scope_reason_text: "",
  worker_scope_observation: "annotatable",
};

function latestState(onChange: ReturnType<typeof vi.fn>): AnnotationState {
  return onChange.mock.calls.at(-1)?.[0] as AnnotationState;
}

describe("AnnotationEditor", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("PAP-ANN-SC-003 keeps top and bottom coordinates independent while dragging in 2D", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();
    const canvas = screen.getByLabelText("全景规范化坐标编辑区");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      bottom: 500,
      height: 500,
      left: 0,
      right: 1000,
      toJSON: () => ({}),
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
    });

    const topPoint = screen.getByLabelText("第 1 对顶点");
    fireEvent.pointerDown(topPoint, { clientX: 100, clientY: 40, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 250, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(canvas, { clientX: 350, clientY: 150, pointerId: 1 });
    fireEvent.pointerUp(canvas, { clientX: 350, clientY: 150, pointerId: 1 });

    await waitFor(() =>
      expect(latestState(onChange).pairs[0].top).toMatchObject({ u: 0.35, v: 0.3 }),
    );
    expect(latestState(onChange).pairs[0].bottom.u).toBe(0.12);
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveAttribute(
      "max",
      "0.9999999999999999",
    );
    expect(screen.getByRole("spinbutton", { name: "第 1 对顶点水平坐标" })).toHaveAttribute(
      "step",
      "any",
    );

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(latestState(onChange)).toEqual(initialState));

    fireEvent.pointerDown(topPoint, { clientX: 100, clientY: 40, pointerId: 2 });
    fireEvent.pointerMove(canvas, { clientX: 1200, clientY: 600, pointerId: 2 });
    fireEvent.pointerUp(canvas, { clientX: 1200, clientY: 600, pointerId: 2 });
    await waitFor(() =>
      expect(latestState(onChange).pairs[0].top).toMatchObject({
        u: 0.9999999999999999,
        v: 1,
      }),
    );
  });

  it("PAP-ANN-SC-004 restores deleted stable IDs through undo and removes them again through redo", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "删除第 2 对" }));
    await waitFor(() => expect(latestState(onChange).pairs).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(latestState(onChange)).toEqual(initialState));

    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    await waitFor(() => expect(latestState(onChange).pairs).toHaveLength(1));
  });

  it("adds unique stable IDs without adding derived wall or BEV fields", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "添加角点对" }));
    const canvas = screen.getByLabelText("全景规范化坐标编辑区");
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
      bottom: 500,
      height: 500,
      left: 0,
      right: 1000,
      toJSON: () => ({}),
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
    });
    fireEvent.click(canvas, { clientX: 200, clientY: 50 });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(canvas, { clientX: 220, clientY: 450 });

    await waitFor(() => expect(latestState(onChange).pairs).toHaveLength(3));
    const state = latestState(onChange);
    const added = state.pairs[2];
    expect(new Set([added.pair_id, added.top.point_id, added.bottom.point_id]).size).toBe(3);
    expect(added.pair_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(added.top).toMatchObject({ u: 0.2, v: 0.1 });
    expect(added.bottom).toMatchObject({ u: 0.22, v: 0.9 });
    expect(state).toMatchObject({
      geometry_attempt_status: initialState.geometry_attempt_status,
      portals: initialState.portals,
      scope_reason_codes: initialState.scope_reason_codes,
      scope_reason_text: initialState.scope_reason_text,
      worker_scope_observation: initialState.worker_scope_observation,
    });
    expect(JSON.stringify(state)).not.toMatch(/wall|bev|mesh/i);
  });

  it("PAP-ANN-SC-007 reorders pairs, preserves IDs and coordinates, changes the hash, and edits the seam", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);
    const hash = await screen.findByTestId("annotation-state-sha");
    await waitFor(() => expect(hash.textContent).toMatch(/^[0-9a-f]{64}$/));
    const originalHash = hash.textContent;

    fireEvent.click(screen.getByRole("button", { name: "第 1 对下移" }));

    await waitFor(() =>
      expect(latestState(onChange).pairs[0].pair_id).toBe(initialState.pairs[1].pair_id),
    );
    const reordered = latestState(onChange);
    expect(reordered.pairs.map(({ order_index }) => order_index)).toEqual([0, 1]);
    expect(reordered.pairs[0].top).toEqual(initialState.pairs[1].top);
    expect(reordered.seam_anchor_pair_id).toBe(initialState.seam_anchor_pair_id);
    await waitFor(() => expect(hash.textContent).not.toBe(originalHash));

    fireEvent.click(screen.getByRole("button", { name: "将第 1 对设为 seam" }));
    await waitFor(() =>
      expect(latestState(onChange).seam_anchor_pair_id).toBe(initialState.pairs[1].pair_id),
    );
  });

  it("edits the POC scope and attempt fields without losing geometry", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);

    fireEvent.change(screen.getByRole("combobox", { name: "Scope / 范围判断" }), {
      target: { value: "representation_oos" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /insufficient_evidence/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Geometry attempt / 几何完成度" }), {
      target: { value: "not_drawable" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "无法绘制说明 / Not drawable explanation" }),
      { target: { value: "Image evidence is insufficient." } },
    );

    await waitFor(() =>
      expect(latestState(onChange)).toMatchObject({
        geometry_attempt_status: "not_drawable",
        geometry_attempt_reason_text: "Image evidence is insufficient.",
        scope_reason_codes: ["insufficient_evidence"],
        worker_scope_observation: "representation_oos",
      }),
    );
    expect(latestState(onChange).pairs).toEqual(initialState.pairs);

    fireEvent.click(screen.getByRole("button", { name: "删除第 2 对" }));
    fireEvent.click(screen.getByRole("button", { name: "删除第 1 对" }));
    await waitFor(() => expect(latestState(onChange).pairs).toEqual([]));
    expect(latestState(onChange).seam_anchor_pair_id).toBeNull();
    expect(latestState(onChange).worker_scope_observation).toBe("representation_oos");
  });

  it("creates a PortalObservation only from explicit four-corner input", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "添加 Portal" }));
    for (const [name, value] of [
      ["Portal 左上 u", "0.2"],
      ["Portal 左上 v", "0.3"],
      ["Portal 右上 u", "0.3"],
      ["Portal 右上 v", "0.3"],
      ["Portal 左下 u", "0.2"],
      ["Portal 左下 v", "0.8"],
      ["Portal 右下 u", "0.3"],
      ["Portal 右下 v", "0.8"],
    ]) {
      fireEvent.change(screen.getByRole("spinbutton", { name }), { target: { value } });
    }
    fireEvent.change(screen.getByRole("combobox", { name: "Portal 类型" }), {
      target: { value: "architectural_opening" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Portal 证据状态" }), {
      target: { value: "direct_visible" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Portal" }));

    await waitFor(() => expect(latestState(onChange).portals).toHaveLength(1));
    expect(latestState(onChange).portals[0]).toMatchObject({
      evidence_status: "direct_visible",
      geometry: {
        bottom_left: { u: 0.2, v: 0.8 },
        bottom_right: { u: 0.3, v: 0.8 },
        top_left: { u: 0.2, v: 0.3 },
        top_right: { u: 0.3, v: 0.3 },
      },
      host_edge_ref: null,
      kind: "architectural_opening",
    });
    expect(Object.keys(latestState(onChange).portals[0]).sort()).toEqual([
      "evidence_status",
      "geometry",
      "host_edge_ref",
      "kind",
      "portal_id",
    ]);

    const portalId = latestState(onChange).portals[0].portal_id;
    fireEvent.click(screen.getByRole("button", { name: "编辑 Portal 1" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Portal 左上 u" }), {
      target: { value: "0.25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Portal" }));
    await waitFor(() => expect(latestState(onChange).portals[0].geometry.top_left.u).toBe(0.25));
    expect(latestState(onChange).portals[0].portal_id).toBe(portalId);
  });

  it("blocks assignment switching while a point pair or Portal is only partially entered", () => {
    const onTransientEditingChange = vi.fn();
    render(
      <AnnotationEditor
        initialState={initialState}
        onChange={vi.fn()}
        onTransientEditingChange={onTransientEditingChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加角点对" }));
    expect(onTransientEditingChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "取消添加" }));
    expect(onTransientEditingChange).toHaveBeenLastCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "添加 Portal" }));
    expect(onTransientEditingChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "取消 Portal" }));
    expect(onTransientEditingChange).toHaveBeenLastCalledWith(false);
  });

  it("clears a Portal host reference when its pair is deleted in the same undo step", async () => {
    const withPortal = structuredClone(initialState);
    withPortal.portals = [
      {
        evidence_status: "direct_visible",
        geometry: {
          bottom_left: { u: 0.2, v: 0.8 },
          bottom_right: { u: 0.3, v: 0.8 },
          top_left: { u: 0.2, v: 0.3 },
          top_right: { u: 0.3, v: 0.3 },
        },
        host_edge_ref: initialState.pairs[1].pair_id,
        kind: "door",
        portal_id: "00000000-0000-4000-8000-000000000007",
      },
    ];
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={withPortal} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "删除第 2 对" }));
    await waitFor(() => expect(latestState(onChange).portals[0].host_edge_ref).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() =>
      expect(latestState(onChange).portals[0].host_edge_ref).toBe(initialState.pairs[1].pair_id),
    );
  });

  it("clears scope reasons when the observation is reset to unanswered", async () => {
    const onChange = vi.fn();
    render(<AnnotationEditor initialState={initialState} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Scope / 范围判断" }), {
      target: { value: "needs_scope_review" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /insufficient_evidence/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Scope / 范围判断" }), {
      target: { value: "" },
    });

    await waitFor(() => expect(latestState(onChange).worker_scope_observation).toBeNull());
    expect(latestState(onChange).scope_reason_codes).toEqual([]);
  });
});
