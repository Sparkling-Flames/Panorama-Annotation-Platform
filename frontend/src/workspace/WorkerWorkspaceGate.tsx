import { type ReactNode, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api";
import { WorkspaceTabCoordinator } from "./workspaceTabCoordinator";

type WorkspaceState =
  "acquiring" | "editable" | "error" | "local_conflict" | "offline" | "takeover_required";

const RENEW_INTERVAL_MS = 45_000;

export function WorkerWorkspaceGate({ children }: { children?: ReactNode }) {
  const [state, setState] = useState<WorkspaceState>("acquiring");
  const acquireRequest = useRef<AbortController | null>(null);
  const attempt = useRef(0);
  const coordinator = useRef<WorkspaceTabCoordinator | null>(null);
  const mounted = useRef(false);
  const renewalRequest = useRef<AbortController | null>(null);
  const renewTimer = useRef<number | undefined>(undefined);
  const clientInstanceId = useRef(crypto.randomUUID());
  const tabId = useRef(crypto.randomUUID());

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
      if (isCurrent(attemptId) && !response.ok) {
        leaveWorkspace(attemptId, "error");
      }
    } catch {
      if (isCurrent(attemptId) && !controller.signal.aborted) {
        leaveWorkspace(attemptId, "offline");
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

    const currentCoordinator = new WorkspaceTabCoordinator();
    coordinator.current = currentCoordinator;
    void acquireLocalWorkspace();

    return () => {
      mounted.current = false;
      beginAttempt();
      currentCoordinator.release();
      if (coordinator.current === currentCoordinator) {
        coordinator.current = null;
      }
    };
  }, []);

  return (
    <section aria-label="工人工作区">
      {state === "acquiring" ? <p>正在取得工作区租约…</p> : null}
      {state === "editable" ? (
        <>
          <p>工作区可编辑。</p>
          {children}
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
      {state === "offline" ? (
        <div>
          <p>网络中断，工作区不可编辑。</p>
          <button onClick={() => void acquireLocalWorkspace()} type="button">
            恢复后重试
          </button>
        </div>
      ) : null}
    </section>
  );
}
