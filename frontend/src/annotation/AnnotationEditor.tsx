import {
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import {
  annotationStateSha,
  type AnnotationPoint,
  type AnnotationPointPair,
  type AnnotationState,
  type MetaContract,
} from "../annotationState";
import { AnnotationContractFields } from "./AnnotationContractFields";
import { PortalEditor } from "./PortalEditor";
import type { SupportedLocale } from "../locale";

type AnnotationEditorProps = {
  backgroundImageUrl?: string;
  disabled?: boolean;
  initialState: AnnotationState;
  initialHistory?: AnnotationHistory;
  locale?: SupportedLocale;
  metaContract?: MetaContract;
  onBackgroundImageError?: () => void;
  onChange?: (state: AnnotationState) => void;
  onHistoryChange?: (history: AnnotationHistory) => void;
  onTransientEditingChange?: (editing: boolean) => void;
};

export type AnnotationHistory = {
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
type MagnifierFocus = NormalizedPoint & { label: string };
const CANVAS_WIDTH = 1000;
const CANVAS_HEIGHT = 500;
const MAX_U = 0.9999999999999999;
const MAGNIFIER_RADIUS = 72;
const MAGNIFIER_SCALE = 4;

function PointMagnifier({
  backgroundImageUrl,
  clipPathId,
  focus,
}: {
  backgroundImageUrl?: string;
  clipPathId: string;
  focus: MagnifierFocus;
}) {
  const focusX = focus.u * CANVAS_WIDTH;
  const focusY = focus.v * CANVAS_HEIGHT;
  const lensX =
    focusX <= CANVAS_WIDTH / 2
      ? Math.min(CANVAS_WIDTH - MAGNIFIER_RADIUS - 8, focusX + 112)
      : Math.max(MAGNIFIER_RADIUS + 8, focusX - 112);
  const lensY = Math.min(
    CANVAS_HEIGHT - MAGNIFIER_RADIUS - 8,
    Math.max(MAGNIFIER_RADIUS + 8, focusY),
  );

  return (
    <g
      aria-label={`${focus.label}局部放大镜`}
      data-focus-u={focus.u}
      data-focus-v={focus.v}
      pointerEvents="none"
      role="img"
    >
      <defs>
        <clipPath id={clipPathId}>
          <circle cx={lensX} cy={lensY} r={MAGNIFIER_RADIUS} />
        </clipPath>
      </defs>
      <circle cx={lensX} cy={lensY} fill="#fffdf8" r={MAGNIFIER_RADIUS} />
      {backgroundImageUrl ? (
        <image
          clipPath={`url(#${clipPathId})`}
          crossOrigin="anonymous"
          height={CANVAS_HEIGHT * MAGNIFIER_SCALE}
          href={backgroundImageUrl}
          preserveAspectRatio="none"
          width={CANVAS_WIDTH * MAGNIFIER_SCALE}
          x={lensX - focusX * MAGNIFIER_SCALE}
          y={lensY - focusY * MAGNIFIER_SCALE}
        />
      ) : null}
      <circle
        cx={lensX}
        cy={lensY}
        fill="none"
        r={MAGNIFIER_RADIUS}
        stroke="#15221d"
        strokeWidth="5"
      />
      <line
        stroke="#f7c948"
        strokeWidth="3"
        x1={lensX - 14}
        x2={lensX + 14}
        y1={lensY}
        y2={lensY}
      />
      <line
        stroke="#f7c948"
        strokeWidth="3"
        x1={lensX}
        x2={lensX}
        y1={lensY - 14}
        y2={lensY + 14}
      />
      <text
        fill="#15221d"
        fontSize="18"
        fontWeight="700"
        textAnchor="middle"
        x={lensX}
        y={Math.min(CANVAS_HEIGHT - 8, lensY + MAGNIFIER_RADIUS + 24)}
      >
        {`${focus.label} · u ${focus.u.toFixed(4)} · v ${focus.v.toFixed(4)}`}
      </text>
    </g>
  );
}

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

export function AnnotationEditor({
  backgroundImageUrl,
  disabled = false,
  initialState,
  locale = "zh-CN",
  initialHistory,
  metaContract,
  onBackgroundImageError,
  onChange,
  onHistoryChange,
  onTransientEditingChange,
}: AnnotationEditorProps) {
  const [history, setHistory] = useState<AnnotationHistory>(
    initialHistory ?? { future: [], past: [], present: initialState },
  );
  const [pendingPair, setPendingPair] = useState<PendingPair | null>(null);
  const [pendingPreview, setPendingPreview] = useState<NormalizedPoint | null>(null);
  const [portalEditing, setPortalEditing] = useState(false);
  const [dragPreview, setDragPreview] = useState<DragPoint | null>(null);
  const [stateSha, setStateSha] = useState("");
  const magnifierClipId = `point-magnifier-${useId().replaceAll(":", "")}`;
  const drag = useRef<DragPoint | null>(null);
  const onChangeRef = useRef(onChange);
  const lastReportedState = useRef(initialState);
  const state = history.present;
  onChangeRef.current = onChange;

  useEffect(
    () => onTransientEditingChange?.(pendingPair !== null || portalEditing),
    [onTransientEditingChange, pendingPair, portalEditing],
  );

  useEffect(() => onHistoryChange?.(history), [history, onHistoryChange]);

  useEffect(() => {
    if (lastReportedState.current !== state) {
      lastReportedState.current = state;
      onChangeRef.current?.(state);
    }
    let active = true;
    void annotationStateSha(state, metaContract).then((sha) => {
      if (active) {
        setStateSha(sha);
      }
    });
    return () => {
      active = false;
    };
  }, [metaContract, state]);

  function commit(update: (current: AnnotationState) => AnnotationState): void {
    if (disabled) return;
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
      const pairs = reindex(current.pairs.filter((pair) => pair.pair_id !== pairId));
      return {
        ...current,
        pairs,
        portals: current.portals.map((portal) =>
          portal.host_edge_ref === pairId ? { ...portal, host_edge_ref: null } : portal,
        ),
        seam_anchor_pair_id: pairs.some((pair) => pair.pair_id === current.seam_anchor_pair_id)
          ? current.seam_anchor_pair_id
          : (pairs[0]?.pair_id ?? null),
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
    if (disabled || pendingPair !== null) {
      return;
    }
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const next = { pairId, point, pointerId: event.pointerId, ...coordinates };
    setPendingPreview(null);
    drag.current = next;
    setDragPreview(next);
  }

  function moveDrag(event: ReactPointerEvent<SVGSVGElement>): void {
    if (drag.current === null) {
      if (pendingPair !== null) {
        setPendingPreview(normalizedPoint(event));
      }
      return;
    }
    if (drag.current.pointerId !== event.pointerId) {
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
    setPendingPreview(null);
  }

  function cancelDrag(): void {
    drag.current = null;
    setDragPreview(null);
    setPendingPreview(null);
  }

  function addPoint(event: ReactMouseEvent<SVGSVGElement>): void {
    if (disabled || pendingPair === null) {
      return;
    }
    const point = normalizedPoint(event);
    if (point === null) {
      return;
    }
    if (pendingPair.top === undefined) {
      setPendingPair({ top: point });
      setPendingPreview(null);
      return;
    }
    const top = pendingPair.top;
    commit((current) => {
      const pair = newPair(current.pairs.length, top, point);
      return {
        ...current,
        pairs: [...current.pairs, pair],
        seam_anchor_pair_id: current.seam_anchor_pair_id ?? pair.pair_id,
      };
    });
    setPendingPair(null);
    setPendingPreview(null);
  }

  function displayedPoint(pair: AnnotationPointPair, point: PointKey): AnnotationPoint {
    return dragPreview?.pairId === pair.pair_id && dragPreview.point === point
      ? { ...pair[point], u: dragPreview.u, v: dragPreview.v }
      : pair[point];
  }

  let magnifierFocus: MagnifierFocus | null = null;
  if (dragPreview !== null) {
    const pairIndex = state.pairs.findIndex((pair) => pair.pair_id === dragPreview.pairId);
    const pointLabel = dragPreview.point === "top" ? "顶点" : "底点";
    magnifierFocus = {
      label: `第 ${pairIndex + 1} 对${pointLabel}`,
      u: dragPreview.u,
      v: dragPreview.v,
    };
  } else if (pendingPair !== null && pendingPreview !== null) {
    magnifierFocus = {
      label: pendingPair.top === undefined ? "待创建角点对顶点" : "待创建角点对底点",
      ...pendingPreview,
    };
  }

  return (
    <section aria-label="2D 角点对编辑器">
      <h2>2D 角点对编辑器</h2>
      <fieldset disabled={disabled} style={{ border: 0, margin: 0, padding: 0 }}>
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
          <button
            disabled={pendingPair !== null}
            onClick={() => {
              setPendingPair({});
              setPendingPreview(null);
            }}
            type="button"
          >
            添加角点对
          </button>
          {pendingPair !== null ? (
            <button
              onClick={() => {
                setPendingPair(null);
                setPendingPreview(null);
              }}
              type="button"
            >
              取消添加
            </button>
          ) : null}
        </div>
        <AnnotationContractFields
          contract={metaContract}
          locale={locale}
          onChange={(next) => commit(() => next)}
          state={state}
        />
        {pendingPair !== null ? (
          <p>{pendingPair.top === undefined ? "请在画布点选顶点。" : "请在画布点选底点。"}</p>
        ) : null}
        <svg
          aria-label="全景规范化坐标编辑区"
          onClick={addPoint}
          onPointerCancel={cancelDrag}
          onPointerLeave={() => setPendingPreview(null)}
          onPointerMove={moveDrag}
          onPointerUp={finishDrag}
          style={{ border: "1px solid currentColor", touchAction: "none", width: "100%" }}
          viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
        >
          {backgroundImageUrl ? (
            <image
              aria-label="当前任务全景图标注底图"
              crossOrigin="anonymous"
              height={CANVAS_HEIGHT}
              href={backgroundImageUrl}
              onError={onBackgroundImageError}
              preserveAspectRatio="none"
              width={CANVAS_WIDTH}
            />
          ) : (
            <rect fill="#eef2ef" height={CANVAS_HEIGHT} width={CANVAS_WIDTH} />
          )}
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
                />
              </g>
            );
          })}
          {state.portals.map((portal) => (
            <polygon
              aria-label={`Portal ${portal.portal_id}`}
              fill="rgba(255, 193, 7, 0.2)"
              key={portal.portal_id}
              points={[
                portal.geometry.top_left,
                portal.geometry.top_right,
                portal.geometry.bottom_right,
                portal.geometry.bottom_left,
              ]
                .map(({ u, v }) => `${u * CANVAS_WIDTH},${v * CANVAS_HEIGHT}`)
                .join(" ")}
              stroke="#b36b00"
              strokeWidth="3"
            />
          ))}
          {pendingPair?.top !== undefined ? (
            <circle
              aria-label="待创建角点对顶点"
              cx={pendingPair.top.u * CANVAS_WIDTH}
              cy={pendingPair.top.v * CANVAS_HEIGHT}
              fill="#2f6fed"
              r="11"
            />
          ) : null}
          {magnifierFocus !== null ? (
            <PointMagnifier
              backgroundImageUrl={backgroundImageUrl}
              clipPathId={magnifierClipId}
              focus={magnifierFocus}
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
                <button onClick={() => deletePair(pair.pair_id)} type="button">
                  删除第 {index + 1} 对
                </button>
              </div>
            </li>
          ))}
        </ol>
        <PortalEditor
          onChange={(next) => commit(() => next)}
          onEditingChange={setPortalEditing}
          state={state}
        />
      </fieldset>
      <output data-testid="annotation-state-sha" hidden>
        {stateSha}
      </output>
    </section>
  );
}
