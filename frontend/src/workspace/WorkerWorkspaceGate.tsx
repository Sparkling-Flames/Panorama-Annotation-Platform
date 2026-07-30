import { useEffect, useRef, useState } from "react";

import { apiFetch } from "../api";
import { WorkspaceTabCoordinator } from "./workspaceTabCoordinator";

type WorkspaceState =
  "acquiring" | "editable" | "error" | "local_conflict" | "offline" | "takeover_required";

const RENEW_INTERVAL_MS = 45_000;

export function WorkerWorkspaceGate() {
  const [state, setState] = useState<WorkspaceState>("acquiring");
  const coordinator = useRef<WorkspaceTabCoordinator | null>(null);
  const mounted = useRef(false);
  const renewTimer = useRef<number | undefined>(undefined);
  const clientInstanceId = useRef(crypto.randomUUID());
  const tabId = useRef(crypto.randomUUID());

  function stopRenewal(): void {
    if (renewTimer.current !== undefined) {
      window.clearInterval(renewTimer.current);
      renewTimer.current = undefined;
    }
  }

  async function renewWorkspace(): Promise<void> {
    try {
      const response = await apiFetch("/api/workspace/renew", {
        body: JSON.stringify({ tab_id: tabId.current }),
        method: "POST",
      });
      if (!response.ok && mounted.current) {
        stopRenewal();
        coordinator.current?.release();
        setState("error");
      }
    } catch {
      if (mounted.current) {
        stopRenewal();
        coordinator.current?.release();
        setState("offline");
      }
    }
  }

  function startRenewal(): void {
    stopRenewal();
    renewTimer.current = window.setInterval(() => void renewWorkspace(), RENEW_INTERVAL_MS);
  }

  async function acquireServerWorkspace(takeover: boolean): Promise<void> {
    setState("acquiring");
    try {
      const response = await apiFetch("/api/workspace/acquire", {
        body: JSON.stringify({
          client_instance_id: clientInstanceId.current,
          tab_id: tabId.current,
          takeover,
        }),
        method: "POST",
      });
      if (!mounted.current) {
        return;
      }
      if (response.ok) {
        setState("editable");
        startRenewal();
        return;
      }
      const payload = (await response.json()) as { error?: { code?: string } };
      if (payload.error?.code === "workspace_takeover_required") {
        setState("takeover_required");
        return;
      }
      coordinator.current?.release();
      setState(payload.error?.code === "workspace_tab_conflict" ? "local_conflict" : "error");
    } catch {
      if (mounted.current) {
        coordinator.current?.release();
        setState("error");
      }
    }
  }

  async function acquireLocalWorkspace(): Promise<void> {
    const currentCoordinator = coordinator.current;
    if (currentCoordinator === null) {
      setState("error");
      return;
    }
    setState("acquiring");
    try {
      const localState = await currentCoordinator.acquire();
      if (!mounted.current) {
        currentCoordinator.release();
      } else if (localState === "conflict") {
        setState("local_conflict");
      } else {
        await acquireServerWorkspace(false);
      }
    } catch {
      if (mounted.current) {
        setState("error");
      }
    }
  }

  useEffect(() => {
    mounted.current = true;
    if (!("locks" in navigator)) {
      setState("error");
      return () => {
        mounted.current = false;
      };
    }

    const currentCoordinator = new WorkspaceTabCoordinator();
    coordinator.current = currentCoordinator;
    void acquireLocalWorkspace();

    return () => {
      mounted.current = false;
      stopRenewal();
      currentCoordinator.release();
    };
  }, []);

  return (
    <section aria-label="工人工作区">
      {state === "acquiring" ? <p>正在取得工作区租约…</p> : null}
      {state === "editable" ? <p>工作区可编辑。</p> : null}
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
