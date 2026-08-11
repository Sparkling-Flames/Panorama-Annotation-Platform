import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnnotationEditor } from "../annotation/AnnotationEditor";
import type { AnnotationState } from "../annotationState";
import { PocWireframe } from "./AssignmentWorkspace";

const state: AnnotationState = {
  geometry_attempt_reason_text: "",
  geometry_attempt_status: "best_effort_complete",
  pairs: [
    {
      bottom: {
        point_id: "00000000-0000-4000-8000-000000000003",
        u: 0.12,
        v: 0.88,
      },
      order_index: 0,
      pair_id: "00000000-0000-4000-8000-000000000001",
      top: {
        point_id: "00000000-0000-4000-8000-000000000002",
        u: 0.1,
        v: 0.08,
      },
    },
  ],
  portals: [],
  seam_anchor_pair_id: "00000000-0000-4000-8000-000000000001",
  scope_reason_codes: [],
  scope_reason_text: "",
  worker_scope_observation: "annotatable",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

describe("PocWireframe", () => {
  beforeEach(() => vi.useFakeTimers());

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("PAP-PRV-SC-010 PAP-PRV-SC-014 binds an informational result to the current state hash", async () => {
    const original = structuredClone(state);
    const hashState = vi.fn().mockResolvedValue("a".repeat(64));

    render(<PocWireframe hashState={hashState} state={state} suspended={false} />);
    await act(() => vi.advanceTimersByTimeAsync(149));
    expect(hashState).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));

    const preview = screen.getByLabelText("本地 3D 预览（仅供参考）");
    expect(preview).toHaveAttribute("data-authority", "informational");
    expect(preview).toHaveAttribute("data-engine-version", "poc-wireframe-v1");
    expect(preview).toHaveAttribute("data-state-sha", "a".repeat(64));
    expect(preview).toHaveAttribute("data-preview-status", "current");
    expect(state).toEqual(original);
  });

  it("PAP-PRV-SC-003 discards an older async hash", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const hashState = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const moved = structuredClone(state);
    moved.pairs[0].top.u = 0.4;
    const view = render(<PocWireframe hashState={hashState} state={state} suspended={false} />);
    await act(() => vi.advanceTimersByTimeAsync(150));
    expect(hashState).toHaveBeenCalledTimes(1);

    view.rerender(<PocWireframe hashState={hashState} state={moved} suspended />);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(hashState).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve("a".repeat(64)));
    expect(screen.getByLabelText("本地 3D 预览（仅供参考）")).not.toHaveAttribute("data-state-sha");

    view.rerender(<PocWireframe hashState={hashState} state={moved} suspended={false} />);
    await act(() => vi.advanceTimersByTimeAsync(150));
    await act(async () => second.resolve("b".repeat(64)));
    const preview = screen.getByLabelText("本地 3D 预览（仅供参考）");
    expect(preview).toHaveAttribute("data-state-sha", "b".repeat(64));
    expect(view.container.querySelector("line")).toHaveAttribute("x1", "120");
  });

  it("PAP-PRV-SC-004 marks the previous result stale after a completed edit", async () => {
    const pending = deferred<string>();
    const hashState = vi
      .fn()
      .mockResolvedValueOnce("a".repeat(64))
      .mockReturnValueOnce(pending.promise);
    const moved = structuredClone(state);
    moved.pairs[0].top.u = 0.4;
    const view = render(<PocWireframe hashState={hashState} state={state} suspended={false} />);
    await act(() => vi.advanceTimersByTimeAsync(150));

    view.rerender(<PocWireframe hashState={hashState} state={moved} suspended={false} />);
    const preview = screen.getByLabelText("本地 3D 预览（仅供参考）");
    expect(preview).toHaveAttribute("data-preview-status", "stale");
    expect(preview).toHaveAttribute("data-state-sha", "a".repeat(64));
    expect(view.container.querySelector("line")).toHaveAttribute("x1", "30");
  });

  it("PAP-PRV-SC-002 does not rebuild for pointer moves and refreshes after pointerup", async () => {
    const hashState = vi.fn().mockResolvedValue("c".repeat(64));

    function Harness() {
      const [current, setCurrent] = useState(state);
      const [suspended, setSuspended] = useState(false);
      return (
        <>
          <AnnotationEditor
            initialState={state}
            onChange={setCurrent}
            onTransientEditingChange={setSuspended}
          />
          <PocWireframe hashState={hashState} state={current} suspended={suspended} />
        </>
      );
    }

    render(<Harness />);
    await act(() => vi.advanceTimersByTimeAsync(150));
    expect(hashState).toHaveBeenCalledTimes(1);
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
    fireEvent.pointerMove(canvas, { clientX: 350, clientY: 150, pointerId: 1 });
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(hashState).toHaveBeenCalledTimes(1);

    fireEvent.pointerUp(canvas, { clientX: 350, clientY: 150, pointerId: 1 });
    await act(() => vi.advanceTimersByTimeAsync(150));
    expect(hashState).toHaveBeenCalledTimes(2);
  });

  it("PAP-PRV-SC-013 reports a local generation error without mutating canonical state", async () => {
    const original = structuredClone(state);
    const hashState = vi.fn().mockRejectedValue(new Error("digest unavailable"));

    render(<PocWireframe hashState={hashState} state={state} suspended={false} />);
    await act(() => vi.advanceTimersByTimeAsync(150));

    expect(screen.getByRole("alert")).toHaveTextContent("仍可保存和提交");
    expect(screen.getByLabelText("本地 3D 预览（仅供参考）")).toHaveAttribute(
      "data-preview-status",
      "error",
    );
    expect(state).toEqual(original);
  });
});
