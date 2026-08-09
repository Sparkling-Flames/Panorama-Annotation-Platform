import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationState } from "../annotationState";
import { AnnotationEditor } from "./AnnotationEditor";

const initialState: AnnotationState = {
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
  seam_anchor_pair_id: "00000000-0000-4000-8000-000000000001",
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

    const topPoint = screen.getByRole("button", { name: "第 1 对顶点" });
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
    expect(Object.keys(state).sort()).toEqual(["pairs", "seam_anchor_pair_id"]);
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
});
