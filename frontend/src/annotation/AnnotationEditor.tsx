import {
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  annotationStateSha,
  type AnnotationPoint,
  type AnnotationPointPair,
  type AnnotationState,
} from "../annotationState";

type AnnotationEditorProps = {
  initialState: AnnotationState;
  onChange?: (state: AnnotationState) => void;
};

type History = {
  future: AnnotationState[];
  past: AnnotationState[];
  present: AnnotationState;
};

type CoordinateKey = "u" | "v";
type PointKey = "bottom" | "top";
type NormalizedPoint = Pick<AnnotationPoint, "u" | "v">;
type PendingPair = { top?: NormalizedPoint };
type DragPoint = NormalizedPoint & {
  pairId: string;
  point: PointKey;
  pointerId: number;
};

const CANVAS_WIDTH = 1000;
const CANVAS_HEIGHT = 500;
const MAX_U = 0.9999999999999999;

function reindex(pairs: AnnotationPointPair[]): AnnotationPointPair[] {
  return pairs.map((pair, order_index) => ({ ...pair, order_index }));
}

function newPair(
  order_index: number,
  top: NormalizedPoint,
  bottom: NormalizedPoint,
): AnnotationPointPair {
  return {
    bottom: { point_id: crypto.randomUUID(), ...bottom },
    order_index,
    pair_id: crypto.randomUUID(),
    top: { point_id: crypto.randomUUID(), ...top },
  };
}

function normalizedPoint(
  event: ReactMouseEvent<SVGSVGElement> | ReactPointerEvent<SVGSVGElement>,
): NormalizedPoint | null {
  const bounds = event.currentTarget.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) {
    return null;
  }
  return {
    u: Math.min(MAX_U, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
    v: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
  };
}

function updatePoint(
  state: AnnotationState,
  pairId: string,
  point: PointKey,
  coordinates: NormalizedPoint,
): AnnotationState {
  const pair = state.pairs.find((candidate) => candidate.pair_id === pairId);
  if (pair === undefined || (pair[point].u === coordinates.u && pair[point].v === coordinates.v)) {
    return state;
  }
  return {
    ...state,
    pairs: state.pairs.map((candidate) =>
      candidate.pair_id === pairId
        ? { ...candidate, [point]: { ...candidate[point], ...coordinates } }
        : candidate,
    ),
  };
}

export function AnnotationEditor({ initialState, onChange }: AnnotationEditorProps) {
  const [history, setHistory] = useState<History>({
    future: [],
    past: [],
    present: initialState,
  });
  const [pendingPair, setPendingPair] = useState<PendingPair | null>(null);
  const [dragPreview, setDragPreview] = useState<DragPoint | null>(null);
  const [stateSha, setStateSha] = useState("");
  const drag = useRef<DragPoint | null>(null);
  const onChangeRef = useRef(onChange);
  const lastReportedState = useRef(initialState);
  const state = history.present;
  onChangeRef.current = onChange;

  useEffect(() => {
    if (lastReportedState.current !== state) {
      lastReportedState.current = state;
      onChangeRef.current?.(state);
    }
    let active = true;
    void annotationStateSha(state).then((sha) => {
      if (active) {
        setStateSha(sha);
      }
    });
    return () => {
      active = false;
    };
  }, [state]);

  function commit(update: (current: AnnotationState) => AnnotationState): void {
    setHistory((current) => {
      const present = update(current.present);
      return present === current.present
        ? current
        : {
            future: [],
            past: [...current.past, current.present],
            present,
          };
    });
  }

  function updateCoordinate(
    pairId: string,
    point: PointKey,
    coordinate: CoordinateKey,
    value: number,
  ): void {
    const maximum = coordinate === "u" ? MAX_U : 1;
    if (!Number.isFinite(value) || value < 0 || value > maximum) {
      return;
    }
    commit((current) => {
      const pair = current.pairs.find((candidate) => candidate.pair_id === pairId);
      return pair === undefined
        ? current
        : updatePoint(current, pairId, point, { ...pair[point], [coordinate]: value });
    });
  }

  function deletePair(pairId: string): void {
    commit((current) => {
      if (current.pairs.length === 1) {
        return current;
      }
      const pairs = reindex(current.pairs.filter((pair) => pair.pair_id !== pairId));
      return {
        pairs,
        seam_anchor_pair_id: pairs.some((pair) => pair.pair_id === current.seam_anchor_pair_id)
          ? current.seam_anchor_pair_id
          : pairs[0].pair_id,
      };
    });
  }

  function movePair(index: number, offset: -1 | 1): void {
    commit((current) => {
      const target = index + offset;
      if (target < 0 || target >= current.pairs.length) {
        return current;
      }
      const pairs = [...current.pairs];
      [pairs[index], pairs[target]] = [pairs[target], pairs[index]];
      return { ...current, pairs: reindex(pairs) };
    });
  }

  function undo(): void {
    setHistory((current) => {
      const present = current.past.at(-1);
      return present === undefined
        ? current
        : {
            future: [current.present, ...current.future],
            past: current.past.slice(0, -1),
            present,
          };
    });
  }

  function redo(): void {
    setHistory((current) => {
      const [present, ...future] = current.future;
      return present === undefined
        ? current
        : {
            future,
            past: [...current.past, current.present],
            present,
          };
    });
  }

  function startDrag(
    event: ReactPointerEvent<SVGCircleElement>,
    pairId: string,
    point: PointKey,
    coordinates: NormalizedPoint,
  ): void {
    if (pendingPair !== null) {
      return;
    }
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const next = { pairId, point, pointerId: event.pointerId, ...coordinates };
    drag.current = next;
    setDragPreview(next);
  }

  function moveDrag(event: ReactPointerEvent<SVGSVGElement>): void {
    if (drag.current === null || drag.current.pointerId !== event.pointerId) {
      return;
    }
    const coordinates = normalizedPoint(event);
    if (coordinates !== null) {
      const next = { ...drag.current, ...coordinates };
      drag.current = next;
      setDragPreview(next);
    }
  }

  function finishDrag(event: ReactPointerEvent<SVGSVGElement>): void {
    if (drag.current === null || drag.current.pointerId !== event.pointerId) {
      return;
    }
    const coordinates = normalizedPoint(event);
    const currentDrag = coordinates === null ? drag.current : { ...drag.current, ...coordinates };
    commit((current) =>
      updatePoint(current, currentDrag.pairId, currentDrag.point, {
        u: currentDrag.u,
        v: currentDrag.v,
      }),
    );
    drag.current = null;
    setDragPreview(null);
  }

  function cancelDrag(): void {
    drag.current = null;
    setDragPreview(null);
  }

  function addPoint(event: ReactMouseEvent<SVGSVGElement>): void {
    if (pendingPair === null) {
      return;
    }
    const point = normalizedPoint(event);
    if (point === null) {
      return;
    }
    if (pendingPair.top === undefined) {
      setPendingPair({ top: point });
      return;
    }
    const top = pendingPair.top;
    commit((current) => ({
      ...current,
      pairs: [...current.pairs, newPair(current.pairs.length, top, point)],
    }));
    setPendingPair(null);
  }

  function displayedPoint(pair: AnnotationPointPair, point: PointKey): AnnotationPoint {
    return dragPreview?.pairId === pair.pair_id && dragPreview.point === point
      ? { ...pair[point], u: dragPreview.u, v: dragPreview.v }
      : pair[point];
  }

  return (
    <section aria-label="2D 角点对编辑器">
      <h2>2D 角点对编辑器</h2>
      <div>
        <button
          disabled={history.past.length === 0 || pendingPair !== null}
          onClick={undo}
          type="button"
        >
          撤销
        </button>
        <button
          disabled={history.future.length === 0 || pendingPair !== null}
          onClick={redo}
          type="button"
        >
          重做
        </button>
        <button disabled={pendingPair !== null} onClick={() => setPendingPair({})} type="button">
          添加角点对
        </button>
        {pendingPair !== null ? (
          <button onClick={() => setPendingPair(null)} type="button">
            取消添加
          </button>
        ) : null}
      </div>
      {pendingPair !== null ? (
        <p>{pendingPair.top === undefined ? "请在画布点选顶点。" : "请在画布点选底点。"}</p>
      ) : null}
      <svg
        aria-label="全景规范化坐标编辑区"
        onClick={addPoint}
        onPointerCancel={cancelDrag}
        onPointerMove={moveDrag}
        onPointerUp={finishDrag}
        role="application"
        style={{ border: "1px solid currentColor", touchAction: "none", width: "100%" }}
        viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
      >
        <rect fill="#eef2ef" height={CANVAS_HEIGHT} width={CANVAS_WIDTH} />
        {state.pairs.map((pair, index) => {
          const top = displayedPoint(pair, "top");
          const bottom = displayedPoint(pair, "bottom");
          return (
            <g key={pair.pair_id}>
              <line
                stroke="#285b46"
                strokeWidth="3"
                x1={top.u * CANVAS_WIDTH}
                x2={bottom.u * CANVAS_WIDTH}
                y1={top.v * CANVAS_HEIGHT}
                y2={bottom.v * CANVAS_HEIGHT}
              />
              <circle
                aria-label={`第 ${index + 1} 对顶点`}
                cx={top.u * CANVAS_WIDTH}
                cy={top.v * CANVAS_HEIGHT}
                fill="#2f6fed"
                onPointerDown={(event) =>
                  startDrag(event, pair.pair_id, "top", { u: top.u, v: top.v })
                }
                onLostPointerCapture={cancelDrag}
                r="11"
                role="button"
                tabIndex={0}
              />
              <circle
                aria-label={`第 ${index + 1} 对底点`}
                cx={bottom.u * CANVAS_WIDTH}
                cy={bottom.v * CANVAS_HEIGHT}
                fill="#d74c31"
                onPointerDown={(event) =>
                  startDrag(event, pair.pair_id, "bottom", { u: bottom.u, v: bottom.v })
                }
                onLostPointerCapture={cancelDrag}
                r="11"
                role="button"
                tabIndex={0}
              />
            </g>
          );
        })}
        {pendingPair?.top !== undefined ? (
          <circle
            aria-label="待创建角点对顶点"
            cx={pendingPair.top.u * CANVAS_WIDTH}
            cy={pendingPair.top.v * CANVAS_HEIGHT}
            fill="#2f6fed"
            r="11"
          />
        ) : null}
      </svg>
      <ol>
        {state.pairs.map((pair, index) => (
          <li key={pair.pair_id}>
            <p>
              第 {index + 1} 对 <code>{pair.pair_id}</code>
            </p>
            {(["top", "bottom"] as const).flatMap((point) =>
              (["u", "v"] as const).map((coordinate) => {
                const pointLabel = point === "top" ? "顶点" : "底点";
                const coordinateLabel = coordinate === "u" ? "水平" : "垂直";
                return (
                  <label key={`${point}-${coordinate}`}>
                    {`第 ${index + 1} 对${pointLabel}${coordinateLabel}坐标`}
                    <input
                      aria-label={`第 ${index + 1} 对${pointLabel}${coordinateLabel}坐标`}
                      max={coordinate === "u" ? MAX_U : 1}
                      min={0}
                      onChange={(event) =>
                        updateCoordinate(
                          pair.pair_id,
                          point,
                          coordinate,
                          event.currentTarget.valueAsNumber,
                        )
                      }
                      step="any"
                      type="number"
                      value={displayedPoint(pair, point)[coordinate]}
                    />
                  </label>
                );
              }),
            )}
            <div>
              <button disabled={index === 0} onClick={() => movePair(index, -1)} type="button">
                第 {index + 1} 对上移
              </button>
              <button
                disabled={index === state.pairs.length - 1}
                onClick={() => movePair(index, 1)}
                type="button"
              >
                第 {index + 1} 对下移
              </button>
              <button
                aria-pressed={state.seam_anchor_pair_id === pair.pair_id}
                disabled={state.seam_anchor_pair_id === pair.pair_id}
                onClick={() =>
                  commit((current) => ({ ...current, seam_anchor_pair_id: pair.pair_id }))
                }
                type="button"
              >
                将第 {index + 1} 对设为 seam
              </button>
              <button
                disabled={state.pairs.length === 1}
                onClick={() => deletePair(pair.pair_id)}
                type="button"
              >
                删除第 {index + 1} 对
              </button>
            </div>
          </li>
        ))}
      </ol>
      <output data-testid="annotation-state-sha" hidden>
        {stateSha}
      </output>
    </section>
  );
}
