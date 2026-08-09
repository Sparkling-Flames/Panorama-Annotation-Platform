import { useEffect, useState } from "react";

import { apiFetch } from "./api";
import { AssignmentSwitcher } from "./assignment/AssignmentSwitcher";
import { MediaImportWizard } from "./media/MediaImportWizard";
import { WorkerWorkspaceGate } from "./workspace/WorkerWorkspaceGate";

type SessionState =
  | { authenticated: false }
  | { authenticated: true; must_change_password: boolean; role: "admin" | "worker" };

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);

  useEffect(() => {
    let active = true;
    void apiFetch("/api/auth/session")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("session unavailable");
        }
        const payload = (await response.json()) as SessionState;
        if (active) {
          setSession(payload);
        }
      })
      .catch(() => {
        if (active) {
          setSession({ authenticated: false });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="app-shell">
      <p className="eyebrow">V1 · 独立平台</p>
      <h1>Panorama Annotation Platform</h1>
      <p>OpenSpec 实施中</p>
      {session?.authenticated && session.role === "admin" ? <MediaImportWizard /> : null}
      {session?.authenticated && session.role === "worker" && !session.must_change_password ? (
        <WorkerWorkspaceGate>
          {({ tabId }) => <AssignmentSwitcher tabId={tabId} />}
        </WorkerWorkspaceGate>
      ) : null}
      {session?.authenticated && session.role === "worker" && session.must_change_password ? (
        <p>请先修改临时密码再进入工作区。</p>
      ) : null}
    </main>
  );
}
