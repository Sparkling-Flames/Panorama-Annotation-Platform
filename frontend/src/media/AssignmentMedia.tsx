import { type ReactNode, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api";

type MediaRole = "compressed" | "high_resolution";
type LoadState = "failed" | "loaded" | "loading" | "missing" | "waiting";

export type ActiveAssignmentMedia = {
  coordinate_mapping: "normalized_identity";
  height: number;
  media_variant_id: string;
  role: MediaRole;
  url: string;
  width: number;
};

type MediaPayload = {
  assignment_id: string;
  expires_at: string;
  unavailable_roles: MediaRole[];
  variants: ActiveAssignmentMedia[];
};

const EMPTY_LOAD_STATE: Record<MediaRole, LoadState> = {
  compressed: "missing",
  high_resolution: "missing",
};

function variantFor(payload: MediaPayload | null, role: MediaRole) {
  return payload?.variants.find((variant) => variant.role === role);
}

export function AssignmentMedia({
  assignmentId,
  children,
}: {
  assignmentId: string;
  children?: (media: ActiveAssignmentMedia, onVisibleError: () => void) => ReactNode;
}) {
  const currentAssignment = useRef(assignmentId);
  const renewedRoles = useRef(new Set<MediaRole>());
  const [activeRole, setActiveRole] = useState<MediaRole>("compressed");
  const [blocked, setBlocked] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [loadState, setLoadState] = useState(EMPTY_LOAD_STATE);
  const [payload, setPayload] = useState<MediaPayload | null>(null);
  const [retry, setRetry] = useState(0);
  currentAssignment.current = assignmentId;

  useEffect(() => {
    const controller = new AbortController();
    renewedRoles.current.clear();
    setBlocked(false);
    setDegraded(false);
    setLoadError(false);
    setLoadState(EMPTY_LOAD_STATE);
    setPayload(null);
    void apiFetch(`/api/worker/assignments/${assignmentId}/media`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const body = (await response.json()) as { error?: { code?: string } };
          if (body.error?.code === "image_unavailable") {
            setBlocked(true);
            return;
          }
          throw new Error("media unavailable");
        }
        const next = (await response.json()) as MediaPayload;
        const compressed = variantFor(next, "compressed");
        const high = variantFor(next, "high_resolution");
        if (!compressed && !high) {
          setBlocked(true);
          return;
        }
        setPayload(next);
        setActiveRole(compressed ? "compressed" : "high_resolution");
        setDegraded(next.unavailable_roles.length > 0);
        setLoadState({
          compressed: compressed ? "loading" : "missing",
          high_resolution: high ? (compressed ? "waiting" : "loading") : "missing",
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setLoadError(true);
        }
      });
    return () => controller.abort();
  }, [assignmentId, retry]);

  useEffect(() => {
    if (payload === null) return;
    const remaining = Date.parse(payload.expires_at) - Date.now();
    if (!Number.isFinite(remaining)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => {
        const requestedAssignment = assignmentId;
        void apiFetch(`/api/worker/assignments/${requestedAssignment}/media`, {
          signal: controller.signal,
        })
          .then(async (response) => {
            if (!response.ok) return;
            const next = (await response.json()) as MediaPayload;
            if (
              currentAssignment.current === requestedAssignment &&
              payload.variants.every((variant) => variantFor(next, variant.role))
            ) {
              renewedRoles.current.clear();
              setPayload(next);
              setDegraded(next.unavailable_roles.length > 0);
            }
          })
          .catch(() => undefined);
      },
      Math.max(0, remaining - Math.min(30_000, remaining / 2)),
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [assignmentId, payload?.expires_at]);

  function failRole(role: MediaRole) {
    const otherRole = role === "compressed" ? "high_resolution" : "compressed";
    if (loadState[otherRole] === "failed" || loadState[otherRole] === "missing") {
      setBlocked(true);
    } else {
      setActiveRole(otherRole);
      setDegraded(true);
    }
    setLoadState((current) => ({ ...current, [role]: "failed" }));
  }

  async function renew(role: MediaRole) {
    if (renewedRoles.current.has(role)) {
      failRole(role);
      return;
    }
    renewedRoles.current.add(role);
    const requestedAssignment = assignmentId;
    try {
      const response = await apiFetch(`/api/worker/assignments/${requestedAssignment}/media`);
      if (!response.ok) {
        throw new Error("media renewal unavailable");
      }
      const next = (await response.json()) as MediaPayload;
      if (currentAssignment.current !== requestedAssignment || !variantFor(next, role)) {
        if (currentAssignment.current === requestedAssignment) {
          failRole(role);
        }
        return;
      }
      const renewedVariant = variantFor(next, role);
      setPayload((current) =>
        current && renewedVariant
          ? {
              ...current,
              expires_at: next.expires_at,
              variants: current.variants.map((variant) =>
                variant.role === role ? renewedVariant : variant,
              ),
            }
          : current,
      );
    } catch {
      if (currentAssignment.current === requestedAssignment) {
        failRole(role);
      }
    }
  }

  if (blocked) {
    return (
      <section aria-label="Assignment 媒体">
        <p role="alert">image_unavailable：任务图片不可用，已阻止编辑和提交。</p>
        <button onClick={() => setRetry((value) => value + 1)} type="button">
          重试加载媒体
        </button>
      </section>
    );
  }
  if (loadError) {
    return (
      <section aria-label="Assignment 媒体">
        <p role="alert">暂时无法取得媒体凭据。</p>
        <button onClick={() => setRetry((value) => value + 1)} type="button">
          重试加载媒体
        </button>
      </section>
    );
  }
  if (payload === null) {
    return <p>正在加载任务媒体…</p>;
  }

  const compressed = variantFor(payload, "compressed");
  const high = variantFor(payload, "high_resolution");
  const mountHigh = high && (!compressed || loadState.compressed !== "loading");
  const activeVariant = variantFor(payload, activeRole);

  return (
    <section
      aria-label="Assignment 媒体"
      data-coordinate-mapping={activeVariant?.coordinate_mapping}
      data-testid="media-coordinate-space"
    >
      <p>
        当前媒体：{activeRole === "compressed" ? "压缩图" : "高清图"}
        {degraded ? "（降级）" : ""}
      </p>
      {compressed ? (
        <img
          alt="当前任务全景图（压缩）"
          crossOrigin="anonymous"
          hidden={children !== undefined || activeRole !== "compressed"}
          key={compressed.url}
          onError={() => void renew("compressed")}
          onLoad={() =>
            setLoadState((current) => ({
              ...current,
              compressed: "loaded",
              high_resolution:
                current.high_resolution === "waiting" ? "loading" : current.high_resolution,
            }))
          }
          referrerPolicy="no-referrer"
          src={compressed.url}
        />
      ) : null}
      {mountHigh ? (
        <img
          alt="当前任务全景图（高清）"
          crossOrigin="anonymous"
          hidden={children !== undefined || activeRole !== "high_resolution"}
          key={high.url}
          onError={() => void renew("high_resolution")}
          onLoad={() => setLoadState((current) => ({ ...current, high_resolution: "loaded" }))}
          referrerPolicy="no-referrer"
          src={high.url}
        />
      ) : null}
      {activeRole === "compressed" && loadState.high_resolution === "loaded" ? (
        <button onClick={() => setActiveRole("high_resolution")} type="button">
          切换到高清图
        </button>
      ) : null}
      {activeRole === "high_resolution" && loadState.compressed === "loaded" ? (
        <button onClick={() => setActiveRole("compressed")} type="button">
          切换到压缩图
        </button>
      ) : null}
      {children && activeVariant && loadState[activeRole] === "loaded"
        ? children(activeVariant, () => void renew(activeRole))
        : null}
    </section>
  );
}
