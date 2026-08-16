import { type ReactNode, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api";
import { WorkspaceTabCoordinator } from "./workspaceTabCoordinator";

type WorkspaceState =
  "acquiring" | "editable" | "error" | "local_conflict" | "lost" | "offline" | "takeover_required";

const RENEW_INTERVAL_MS = 45_000;
const CLIENT_INSTANCE_ID_KEY = "panorama.workspace.client-instance-id";
const TAB_ID_KEY = "panorama.workspace.tab-id";

function workspaceSessionId(key: string): string {
  try {
    const existing = window.sessionStorage.getItem(key);
    if (existing !== null) return existing;
    const created = crypto.randomUUID();
    window.sessionStorage.setItem(key, created);
    return created;
  } catch {
    return crypto.randomUUID();
  }
}

type WorkspaceRenderContext = { online: boolean; tabId: string; writable: boolean };

export function WorkerWorkspaceGate({
  children,
}: {
  children?: ReactNode | ((context: WorkspaceRenderContext) => ReactNode);
}) {
  const [state, setState] = useState<WorkspaceState>("acquiring");
  const acquireRequest = useRef<AbortController | null>(null);
  const attempt = useRef(0);
  const coordinator = useRef<WorkspaceTabCoordinator | null>(null);
  const mounted = useRef(false);
  const renewalRequest = useRef<AbortController | null>(null);
  const renewTimer = useRef<number | undefined>(undefined);
  const clientInstanceId = useRef(workspaceSessionId(CLIENT_INSTANCE_ID_KEY));
  const hasEditableWorkspace = useRef(false);
  const leaseLost = useRef(false);
  const tabId = useRef(workspaceSessionId(TAB_ID_KEY));

  function stopRenewal(): void {
    if (renewTimer.current !== undefined) {
      window.clearInterval(renewTimer.current);
      renewTimer.current = undefined;
    }
  }

  function beginAttempt(): number {
    attempt.current += 1;
    acquireRequest.current?.abort();
    acquireRequest.current = null;
    renewalRequest.current?.abort();
    renewalRequest.current = null;
    stopRenewal();
    return attempt.current;
  }

  function isCurrent(attemptId: number): boolean {
    return mounted.current && attempt.current === attemptId;
  }

  function leaveWorkspace(attemptId: number, nextState: WorkspaceState): void {
    if (!isCurrent(attemptId)) {
      return;
    }
    beginAttempt();
    coordinator.current?.release();
    setState(nextState);
  }

  async function renewWorkspace(attemptId: number): Promise<void> {
    if (!isCurrent(attemptId) || renewalRequest.current !== null) {
      return;
    }
    const controller = new AbortController();
    renewalRequest.current = controller;
    try {
      const response = await apiFetch("/api/workspace/renew", {
        body: JSON.stringify({ tab_id: tabId.current }),
        method: "POST",
        signal: controller.signal,
      });
      if (!isCurrent(attemptId)) return;
      if (!response.ok) {
        const payload = (await response.json()) as { error?: { code?: string } };
        if (payload.error?.code === "workspace_lease_lost") {
          leaseLost.current = true;
          stopRenewal();
          setState("lost");
        } else {
          leaveWorkspace(attemptId, "error");
        }
      } else {
        setState("editable");
        startRenewal(attemptId);
      }
    } catch {
      if (isCurrent(attemptId) && !controller.signal.aborted) {
        stopRenewal();
        setState(hasEditableWorkspace.current ? "offline" : "error");
      }
    } finally {
      if (renewalRequest.current === controller) {
        renewalRequest.current = null;
      }
    }
  }

  function startRenewal(attemptId: number): void {
    stopRenewal();
    renewTimer.current = window.setInterval(
      () => void renewWorkspace(attemptId),
      RENEW_INTERVAL_MS,
    );
  }

  async function acquireServerWorkspace(
    takeover: boolean,
    attemptId = beginAttempt(),
  ): Promise<void> {
    if (!isCurrent(attemptId)) {
      return;
    }
    setState("acquiring");
    const controller = new AbortController();
    acquireRequest.current = controller;
    try {
      const response = await apiFetch("/api/workspace/acquire", {
        body: JSON.stringify({
          client_instance_id: clientInstanceId.current,
          tab_id: tabId.current,
          takeover,
        }),
        method: "POST",
        signal: controller.signal,
      });
      if (!isCurrent(attemptId)) {
        return;
      }
      if (response.ok) {
        hasEditableWorkspace.current = true;
        leaseLost.current = false;
        setState("editable");
        startRenewal(attemptId);
        return;
      }
      const payload = (await response.json()) as { error?: { code?: string } };
      if (!isCurrent(attemptId)) {
        return;
      }
      if (payload.error?.code === "workspace_takeover_required") {
        setState("takeover_required");
        return;
      }
      leaveWorkspace(
        attemptId,
        payload.error?.code === "workspace_tab_conflict" ? "local_conflict" : "error",
      );
    } catch {
      if (isCurrent(attemptId) && !controller.signal.aborted) {
        leaveWorkspace(attemptId, "error");
      }
    } finally {
      if (acquireRequest.current === controller) {
        acquireRequest.current = null;
      }
    }
  }

  async function acquireLocalWorkspace(): Promise<void> {
    const attemptId = beginAttempt();
    const currentCoordinator = coordinator.current;
    if (currentCoordinator === null) {
      if (mounted.current) {
        setState("error");
      }
      return;
    }
    setState("acquiring");
    try {
      const localState = await currentCoordinator.acquire();
      if (!isCurrent(attemptId)) {
        return;
      }
      if (localState === "conflict") {
        setState("local_conflict");
      } else {
        await acquireServerWorkspace(false, attemptId);
      }
    } catch {
      if (isCurrent(attemptId)) {
        leaveWorkspace(attemptId, "error");
      }
    }
  }

  useEffect(() => {
    mounted.current = true;
    if (!("locks" in navigator)) {
      setState("error");
      return () => {
        mounted.current = false;
        beginAttempt();
      };
    }

    const currentCoordinator = coordinator.current ?? new WorkspaceTabCoordinator();
    coordinator.current = currentCoordinator;
    const offline = () => {
      if (mounted.current && hasEditableWorkspace.current && !leaseLost.current) {
        stopRenewal();
        setState("offline");
      }
    };
    const online = () => {
      if (mounted.current && hasEditableWorkspace.current && !leaseLost.current) {
        void renewWorkspace(attempt.current);
      }
    };
    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    void acquireLocalWorkspace();

    return () => {
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
      mounted.current = false;
      beginAttempt();
      currentCoordinator.release();
    };
  }, []);

  return (
    <section aria-label="工人工作区">
      {state === "acquiring" ? <p>正在取得工作区租约…</p> : null}
      {state === "editable" || state === "offline" || state === "lost" ? (
        <>
          <p>
            {state === "editable"
              ? "工作区可编辑。"
              : state === "offline"
                ? "离线"
                : "工作区已失效，已保留本地恢复副本。"}
          </p>
          {typeof children === "function"
            ? children({
                online: state === "editable",
                tabId: tabId.current,
                writable: state !== "lost",
              })
            : children}
        </>
      ) : null}
      {state === "local_conflict" ? (
        <div>
          <p>此浏览器已有另一个可编辑标签页。</p>
          <button onClick={() => void acquireLocalWorkspace()} type="button">
            重试进入工作区
          </button>
        </div>
      ) : null}
      {state === "takeover_required" ? (
        <div>
          <p>另一设备持有工作区租约，需要明确接管。</p>
          <button onClick={() => void acquireServerWorkspace(true)} type="button">
            接管工作区
          </button>
        </div>
      ) : null}
      {state === "error" ? <p>无法取得或续租工作区，当前页面不可编辑。</p> : null}
    </section>
  );
}
